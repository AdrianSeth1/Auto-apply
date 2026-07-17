"""Explainable, source-neutral employer assessment for Job Pool V2."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.intake.schema import RawJob
from src.matching.target_schema import normalize_phrase, phrase_in_text

CLASSIFIER_VERSION = "employer-v2.2"


class EmployerAssessmentV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    employment_relationship: Literal[
        "direct_employer", "staffing_intermediary", "employer_of_record", "unknown"
    ] = "unknown"
    business_model: Literal[
        "product_company",
        "consultancy_professional_services",
        "government_contractor",
        "nonprofit_education",
        "public_sector",
        "unknown",
    ] = "unknown"
    lifecycle: Literal["startup", "growth", "established", "unknown"] = "unknown"
    funding_stage: Literal[
        "pre_seed",
        "seed",
        "series_a",
        "series_b",
        "series_c_plus",
        "bootstrapped",
        "public",
        "unknown",
    ] = "unknown"
    selectivity_tier: Literal["exceptional", "high", "standard", "unknown"] = "unknown"
    confidence: float = Field(ge=0, le=1)
    evidence: tuple[str, ...] = ()
    classifier_version: str = CLASSIFIER_VERSION
    assessed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


_STAFFING = re.compile(
    r"(?i)\b(staffing|recruiting|recruitment|talent solutions|placement|headhunt|"
    r"randstad|teksystems|robert half|motion recruitment|insight global|kforce)\b"
)
_CONSULTANCY = re.compile(
    r"(?i)\b(consulting|consultancy|professional services|systems integrator|digital agency)\b"
)
_GOVERNMENT = re.compile(
    r"(?i)\b(government contractor|federal contractor|department of defense|dod contract|"
    r"public sector contractor)\b"
)
_EDUCATION = re.compile(r"(?i)\b(university|college|school|education nonprofit)\b")
_STARTUP = re.compile(
    r"(?i)\b(y combinator|\byc\b|venture[- ]backed|backed by .{0,80}(?:ventures|capital|vc)|"
    r"series [a-f]|seed round|best startups?|startup company|top vcs?|"
    r"raised \$[0-9])\b"
)

_EXCEPTIONAL = {
    "openai",
    "anthropic",
    "palantir",
}
_HIGH = {
    "stripe",
    "figma",
    "databricks",
    "snowflake",
    "cloudflare",
    "notion",
    "linear",
    "ramp",
}
_ESTABLISHED = {
    "salesforce",
    "adobe",
    "microsoft",
    "google",
    "amazon",
    "oracle",
    "workday",
    "ibm",
    "hpe",
    "saic",
}


def assess_employer(job: RawJob) -> EmployerAssessmentV2:
    """Classify independent employer dimensions using persisted job evidence."""

    company = normalize_phrase(job.company)
    text = f"{job.company}\n{job.description or ''}"
    raw = job.raw_data or {}
    evidence: list[str] = []

    if _STAFFING.search(job.company) or bool(raw.get("staffing_agency")):
        relationship = "staffing_intermediary"
        evidence.append("company name or provider metadata identifies a staffing intermediary")
        relationship_confidence = 0.9
    elif job.company and "@" not in job.company:
        relationship = "direct_employer"
        evidence.append("named employer publishes the role")
        relationship_confidence = 0.65
    else:
        relationship = "unknown"
        relationship_confidence = 0.0

    if _GOVERNMENT.search(text):
        business_model = "government_contractor"
        evidence.append("posting identifies government-contract work")
    elif _CONSULTANCY.search(f"{job.company} {raw.get('company_description', '')}"):
        business_model = "consultancy_professional_services"
        evidence.append("employer identifies consulting/professional-services model")
    elif _EDUCATION.search(job.company):
        business_model = "nonprofit_education"
        evidence.append("employer identity is educational")
    elif relationship == "direct_employer":
        business_model = "product_company"
    else:
        business_model = "unknown"

    startup_evidence = bool(raw.get("is_startup")) or raw.get("startup_type") in {
        "yc",
        "venture_backed",
    }
    feed = normalize_phrase(
        str(raw.get("feed") or raw.get("source_feed") or raw.get("hn_feed") or "")
    )
    if feed in {"hn jobstories", "yc jobs", "ycombinator jobs"}:
        startup_evidence = True
        evidence.append(f"startup-native feed: {feed}")
    if _STARTUP.search(text):
        startup_evidence = True
        evidence.append("posting contains explicit venture/startup lifecycle evidence")
    if startup_evidence:
        lifecycle = "startup"
        evidence.append("explicit startup lifecycle evidence")
    elif company in _ESTABLISHED or bool(raw.get("public_company")):
        lifecycle = "established"
        evidence.append("verified established/public employer")
    else:
        lifecycle = "unknown"

    stage_text = normalize_phrase(f"{raw.get('funding_stage') or ''} {text}")
    stage_map = {
        "pre seed": "pre_seed",
        "seed": "seed",
        "series a": "series_a",
        "series b": "series_b",
        "series c": "series_c_plus",
        "series d": "series_c_plus",
        "bootstrapped": "bootstrapped",
        "public": "public",
    }
    funding_stage = next(
        (value for key, value in stage_map.items() if phrase_in_text(key, stage_text)),
        "unknown",
    )

    if company in _EXCEPTIONAL:
        selectivity = "exceptional"
        evidence.append("versioned exceptional-selectivity cohort")
    elif company in _HIGH:
        selectivity = "high"
        evidence.append("versioned high-selectivity cohort")
    elif company and relationship != "staffing_intermediary":
        selectivity = "standard"
    else:
        selectivity = "unknown"

    known_dimensions = sum(
        value != "unknown"
        for value in (relationship, business_model, lifecycle, funding_stage, selectivity)
    )
    confidence = min(0.95, 0.2 + 0.15 * known_dimensions)
    confidence = max(confidence, relationship_confidence)
    return EmployerAssessmentV2(
        employment_relationship=relationship,  # type: ignore[arg-type]
        business_model=business_model,  # type: ignore[arg-type]
        lifecycle=lifecycle,  # type: ignore[arg-type]
        funding_stage=funding_stage,  # type: ignore[arg-type]
        selectivity_tier=selectivity,  # type: ignore[arg-type]
        confidence=confidence,
        evidence=tuple(evidence),
    )


__all__ = ["CLASSIFIER_VERSION", "EmployerAssessmentV2", "assess_employer"]
