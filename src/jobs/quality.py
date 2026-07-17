"""Posting/provenance quality assessment independent of candidate fit."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from src.intake.schema import RawJob
from src.matching.target_schema import normalize_phrase, phrase_in_text

CLASSIFIER_VERSION = "posting-quality-v2.2"
_DIRECT_ATS_HOSTS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "workable.com",
    "recruitee.com",
)


class PostingQualityAssessmentV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    description_completeness: Literal["full", "partial", "snippet", "missing"]
    application_target_kind: Literal[
        "direct_ats",
        "employer_site",
        "aggregator_redirect",
        "recruiter_contact",
        "email",
        "missing",
        "unknown",
    ]
    freshness_score: float = Field(ge=0, le=1)
    employer_identity_confidence: float = Field(ge=0, le=1)
    requisition_specificity: float = Field(ge=0, le=1)
    evergreen_risk: Literal["low", "medium", "high", "unknown"]
    integrity_problems: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    trust_score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    classifier_version: str = CLASSIFIER_VERSION
    assessed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _published_at(job: RawJob) -> datetime | None:
    raw = job.raw_data or {}
    candidates = (
        job.provenance.provider_published_at if job.provenance else None,
        raw.get("publishedAt"),
        raw.get("created"),
        raw.get("publication_date"),
        raw.get("workday_posted_date"),
        raw.get("first_seen_at"),
        job.discovered_at,
    )
    return next((parsed for value in candidates if (parsed := _parse_datetime(value))), None)


def _application_kind(job: RawJob) -> str:
    if job.provenance:
        kind = job.provenance.application_target.kind
        if kind != "unknown":
            return kind
    if not job.application_url:
        return "missing"
    url = job.application_url.strip()
    if url.lower().startswith("mailto:"):
        return "email"
    host = (urlparse(url).hostname or "").casefold()
    if any(host.endswith(domain) for domain in _DIRECT_ATS_HOSTS):
        return "direct_ats"
    if job.source in {"adzuna", "remotive", "hn"}:
        return "aggregator_redirect" if job.source == "adzuna" else "employer_site"
    if host:
        return "employer_site"
    return "unknown"


def assess_posting(job: RawJob, *, employer_confidence: float = 0.0) -> PostingQualityAssessmentV2:
    description = (job.description or "").strip()
    length = len(description)
    declared_completeness = normalize_phrase(
        str((job.raw_data or {}).get("description_completeness") or "")
    )
    if job.source == "adzuna" and not (job.raw_data or {}).get("full_jd_recovered"):
        completeness = "snippet"
        completeness_score = 0.35
    elif declared_completeness in {"full", "partial", "snippet", "missing"}:
        completeness = declared_completeness
        completeness_score = {"full": 1.0, "partial": 0.7, "snippet": 0.35, "missing": 0.0}[
            completeness
        ]
    elif length >= 800:
        completeness = "full"
        completeness_score = 1.0
    elif length >= 300:
        completeness = "partial"
        completeness_score = 0.7
    elif length:
        completeness = "snippet"
        completeness_score = 0.35
    else:
        completeness = "missing"
        completeness_score = 0.0

    kind = _application_kind(job)
    integrity_score = {
        "direct_ats": 1.0,
        "employer_site": 0.9,
        "aggregator_redirect": 0.55,
        "recruiter_contact": 0.45,
        "email": 0.35,
        "unknown": 0.25,
        "missing": 0.0,
    }[kind]
    problems: list[str] = []
    if kind == "missing":
        problems.append("missing_application_target")
    if "@" in (job.company or ""):
        problems.append("company_name_is_email")

    published = _published_at(job)
    if published:
        age_days = max(0, (datetime.now(UTC) - published).days)
        freshness = 1.0 if age_days <= 30 else 0.8 if age_days <= 60 else 0.5 if age_days <= 90 else 0.2
    else:
        age_days = None
        freshness = 0.5

    normalized = normalize_phrase(f"{job.title} {description}")
    generic = any(
        phrase_in_text(term, normalized)
        for term in ("general application", "talent pool", "future opportunities", "multiple roles")
    )
    specificity = 0.2 if generic else (0.9 if len(job.title.strip()) >= 8 and length >= 300 else 0.55)

    evergreen_signals = 0
    if generic:
        evergreen_signals += 1
    if age_days is not None and age_days > 90:
        evergreen_signals += 1
    raw = job.raw_data or {}
    if bool(raw.get("reopened_identical")):
        evergreen_signals += 1
    if bool(raw.get("no_requisition_id")) and kind in {"missing", "unknown"}:
        evergreen_signals += 1
    evergreen = "high" if evergreen_signals >= 2 else "medium" if evergreen_signals == 1 else "low"

    employer_identity = max(0.0, min(1.0, employer_confidence))
    trust = 100 * (
        0.25 * completeness_score
        + 0.20 * integrity_score
        + 0.20 * freshness
        + 0.20 * employer_identity
        + 0.15 * specificity
    )
    confidence_fields = [
        1.0 if length else 0.0,
        0.9 if kind not in {"unknown", "missing"} else 0.2,
        0.9 if published else 0.0,
        employer_identity,
        0.8 if job.title else 0.0,
    ]
    confidence = sum(confidence_fields) / len(confidence_fields)
    evidence = [f"description={completeness}", f"application_target={kind}"]
    if age_days is not None:
        evidence.append(f"posting_age_days={age_days}")
    return PostingQualityAssessmentV2(
        description_completeness=completeness,  # type: ignore[arg-type]
        application_target_kind=kind,  # type: ignore[arg-type]
        freshness_score=freshness,
        employer_identity_confidence=employer_identity,
        requisition_specificity=specificity,
        evergreen_risk=evergreen,  # type: ignore[arg-type]
        integrity_problems=tuple(problems),
        evidence=tuple(evidence),
        trust_score=round(trust, 2),
        confidence=round(confidence, 4),
    )


__all__ = ["CLASSIFIER_VERSION", "PostingQualityAssessmentV2", "assess_posting"]
