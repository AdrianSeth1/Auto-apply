"""Deterministic JobFactsV2 extraction and tri-state global eligibility."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.intake.schema import RawJob
from src.matching.target_schema import ResolvedTargetProfile, normalize_phrase, phrase_in_text

PARSER_VERSION = "job-facts-v2.4"
GateStatus = Literal["pass", "fail", "unknown"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FactSignalV2(_FrozenModel):
    kind: str
    value: str | int | float | bool
    excerpt: str
    confidence: float = Field(ge=0, le=1)


class CapabilityRequirementV2(_FrozenModel):
    capability_id: str
    importance: Literal["mandatory", "core", "preferred"]
    excerpt: str
    confidence: float = Field(ge=0, le=1)


class CompensationFactV2(_FrozenModel):
    currency: str = "USD"
    minimum: int | None = None
    maximum: int | None = None
    period: str = "year"
    confidence: float = Field(default=0.0, ge=0, le=1)


class JobFactsV2(_FrozenModel):
    parser_version: str = PARSER_VERSION
    full_description_available: bool
    title_tokens: tuple[str, ...]
    country_codes: tuple[str, ...] = ()
    workplace_type: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    remote_geographies: tuple[str, ...] = ()
    compensation: CompensationFactV2 | None = None
    level_signals: tuple[FactSignalV2, ...] = ()
    responsibility_signals: tuple[FactSignalV2, ...] = ()
    capability_requirements: tuple[CapabilityRequirementV2, ...] = ()
    domain_signals: tuple[FactSignalV2, ...] = ()
    authorization_signals: tuple[FactSignalV2, ...] = ()
    specialization_signals: tuple[FactSignalV2, ...] = ()
    field_confidence: dict[str, float] = Field(default_factory=dict)


class GateResultV2(_FrozenModel):
    gate_id: str
    status: GateStatus
    reason_code: str
    message: str
    job_evidence: tuple[str, ...] = ()
    confidence: float = Field(ge=0, le=1)


_RESPONSIBILITY_PATTERNS: dict[str, tuple[str, ...]] = {
    "workflow_discovery": ("workflow discovery", "requirements gathering", "needs analysis"),
    "configuration": ("configure", "configuration", "implementation"),
    "onboarding": ("onboarding", "activation", "time to value", "go-live", "go live"),
    "project_delivery": ("project delivery", "deliver projects", "implementation plan"),
    "customer_enablement": ("enablement", "training", "customer education"),
    "technical_discovery": ("technical discovery", "discovery calls", "solution discovery"),
    "demonstrations": ("demo", "demonstration", "proof of concept", "poc"),
    "customer_success": ("customer success", "customer health", "product adoption"),
    "analytics": ("analytics", "dashboards", "reporting", "operational metrics"),
    "process_improvement": ("process improvement", "business process", "optimize workflows"),
    "software_engineering": ("write production code", "backend services", "software development"),
    "ml_research": ("research scientist", "publish research", "train foundation models"),
    "quota_ownership": ("quota", "renewal target", "expansion target"),
    "enterprise_portfolio": ("portfolio of enterprise", "strategic accounts", "book of business"),
}

_CAPABILITY_ALIASES: dict[str, tuple[str, ...]] = {
    "cap_implementation": ("implementation", "configuration", "deploy", "go-live"),
    "cap_discovery": ("discovery", "requirements gathering", "workflow mapping"),
    "cap_onboarding": ("onboarding", "activation", "time to value"),
    "cap_project_delivery": ("project delivery", "project plan", "deliver implementation"),
    "cap_client_facing": ("customer-facing", "client-facing", "work with customers"),
    "cap_stakeholder_translation": ("translate", "technical and non-technical", "stakeholders"),
    "cap_documentation": ("documentation", "implementation guide", "knowledge base"),
    "cap_ai": ("artificial intelligence", "generative ai", "applied ai"),
    "cap_llm": ("large language model", "llm", "language model"),
    "cap_rag": ("retrieval augmented", "rag"),
    "cap_python": ("python",),
    "cap_analytics": ("analytics", "analysis", "metrics"),
    "cap_dashboards": ("dashboard", "business intelligence", "looker"),
    "cap_demos": ("demo", "demonstration", "proof of concept"),
    "cap_customer_success": ("customer success", "customer health", "adoption"),
    "cap_communication": ("communication", "present", "explain"),
    "cap_solution_design": ("solution design", "solution architecture"),
    "cap_process_improvement": ("process improvement", "optimize process"),
    "cap_operations": ("operations", "operational"),
}

_SPECIALIZATIONS = (
    "sap",
    "oracle",
    "workday hcm",
    "salesforce administrator",
    "security clearance",
    "machine learning infrastructure",
    "distributed systems",
    "actuarial",
    "quantitative finance",
    "clinical license",
)

_EXPERIENCE_RE = re.compile(
    r"(?i)(?:minimum\s+(?:of\s+)?)?(\d+)\s*(?:\+|plus)?\s*(?:years?|yrs?)"
)
_PAY_RE = re.compile(
    r"(?i)(?:\$|usd\s*)(\d{2,3}(?:,\d{3})?|\d{2,3})\s*(?:k)?"
    r"(?:\s*(?:-|to)\s*(?:\$|usd\s*)?(\d{2,3}(?:,\d{3})?|\d{2,3})\s*(?:k)?)?"
)
_ACTIVE_CLEARANCE_RE = re.compile(
    r"(?i)(active\s+(?:secret|top secret|ts/sci|dod)\s+clearance|"
    r"(?:security|government)\s+clearance\s+(?:is\s+)?required)"
)
_US_STATE_CODES = frozenset(
    {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
        "DC",
    }
)


def _excerpt(text: str, start: int, end: int, window: int = 90) -> str:
    value = " ".join(text[max(0, start - window) : min(len(text), end + window)].split())
    return value[:260]


def _workplace(job: RawJob, text: str) -> str:
    raw = normalize_phrase(str((job.raw_data or {}).get("workplaceType") or ""))
    location = normalize_phrase(job.location or "")
    if "hybrid" in raw or "hybrid" in location or "hybrid" in text:
        return "hybrid"
    if any(term in raw or term in location for term in ("remote", "work from home")):
        return "remote"
    if any(term in raw or term in location for term in ("onsite", "on site", "in office", "office")):
        return "onsite"
    if re.search(r"\b\d+\s+days?\s+in\s+(?:the\s+)?office\b", text) or phrase_in_text(
        "commuting distance", text
    ):
        return "hybrid"
    if job.requirements.remote_ok is True:
        return "remote"
    if job.requirements.remote_ok is False:
        return "onsite"
    return "unknown"


def _country_codes(job: RawJob, text: str) -> tuple[str, ...]:
    del text  # Description prose is not reliable hiring-geography evidence.
    raw = job.raw_data or {}
    structured = " ".join(
        str(raw.get(key) or "")
        for key in ("country", "country_code", "countryCode", "location_country")
    )
    geography = f"{job.location or ''} {structured}"
    location = f" {normalize_phrase(geography)} "
    state_codes = set(re.findall(r",\s*([A-Z]{2})(?:\b|$)", job.location or ""))
    if any(
        token in location
        for token in (
            " united states ",
            " usa ",
            " u s ",
            " us ",
            " us remote ",
            " remote us ",
        )
    ) or bool(state_codes & _US_STATE_CODES):
        return ("US",)
    return ()


def _remote_location_is_bare(location: str) -> bool:
    normalized = normalize_phrase(location)
    return normalized in {
        "remote",
        "remote only",
        "work from home",
        "anywhere",
        "distributed",
    }


def _compensation(job: RawJob, description: str) -> CompensationFactV2 | None:
    raw = job.raw_data or {}
    minimum = raw.get("salary_min")
    maximum = raw.get("salary_max")
    if isinstance(minimum, int | float) or isinstance(maximum, int | float):
        return CompensationFactV2(
            minimum=int(minimum) if minimum is not None else None,
            maximum=int(maximum) if maximum is not None else None,
            confidence=0.95,
        )
    match = _PAY_RE.search(description)
    if not match:
        return None

    def amount(value: str | None) -> int | None:
        if not value:
            return None
        compact = value.replace(",", "")
        number = int(compact)
        return number * 1000 if number < 1000 else number

    return CompensationFactV2(
        minimum=amount(match.group(1)), maximum=amount(match.group(2)), confidence=0.8
    )


def extract_job_facts(job: RawJob) -> JobFactsV2:
    description = job.description or ""
    combined = f"{job.title}\n{description}"
    normalized = normalize_phrase(combined)

    level_signals: list[FactSignalV2] = []
    experience_matches = []
    for match in _EXPERIENCE_RE.finditer(combined):
        before = combined[max(0, match.start() - 100) : match.start()].casefold()
        after = combined[match.end() : match.end() + 80].casefold()
        context = f"{before} {match.group(0).casefold()} {after}"
        if "years running" in after or re.search(r"(?:last|past)\s+\d+\s*$", before):
            continue
        score = 0
        if "experience" in context:
            score += 4
        if any(term in context for term in ("required", "qualification", "minimum", "must have")):
            score += 2
        if "+" in match.group(0) or "plus" in match.group(0).casefold():
            score += 1
        experience_matches.append((score, match.start(), match))
    for _, _, match in sorted(experience_matches, key=lambda value: (-value[0], value[1])):
        level_signals.append(
            FactSignalV2(
                kind="required_years",
                value=int(match.group(1)),
                excerpt=_excerpt(combined, match.start(), match.end()),
                confidence=0.8,
            )
        )
        break
    seniority = job.requirements.seniority or job.seniority
    if seniority and seniority != "unknown":
        level_signals.append(
            FactSignalV2(
                kind="seniority", value=seniority, excerpt=job.title, confidence=0.8
            )
        )

    responsibilities: list[FactSignalV2] = []
    for responsibility_id, aliases in _RESPONSIBILITY_PATTERNS.items():
        for alias in aliases:
            if phrase_in_text(alias, combined):
                responsibilities.append(
                    FactSignalV2(
                        kind="responsibility",
                        value=responsibility_id,
                        excerpt=alias,
                        confidence=0.8,
                    )
                )
                break

    capabilities: list[CapabilityRequirementV2] = []
    must_text = " ".join(job.requirements.must_have_skills)
    preferred_text = " ".join(job.requirements.preferred_skills)
    for capability_id, aliases in _CAPABILITY_ALIASES.items():
        # A title is routing evidence, not proof that the JD requires a
        # capability. This prevents an empty/thin skill parse from receiving
        # positive evidence merely because the title repeats the target name.
        match_alias = next((alias for alias in aliases if phrase_in_text(alias, description)), None)
        if not match_alias:
            continue
        if any(phrase_in_text(alias, must_text) for alias in aliases):
            importance: Literal["mandatory", "core", "preferred"] = "mandatory"
            confidence = 0.95
        elif any(phrase_in_text(alias, preferred_text) for alias in aliases):
            importance = "preferred"
            confidence = 0.95
        else:
            importance = "core"
            confidence = 0.75
        capabilities.append(
            CapabilityRequirementV2(
                capability_id=capability_id,
                importance=importance,
                excerpt=match_alias,
                confidence=confidence,
            )
        )

    domains = []
    domain = job.requirements.domain
    if domain:
        domains.append(
            FactSignalV2(kind="domain", value=domain, excerpt=domain, confidence=0.8)
        )
    for value in ("b2b saas", "healthcare", "education", "applied ai", "revops"):
        if phrase_in_text(value, combined):
            domains.append(
                FactSignalV2(kind="domain", value=value, excerpt=value, confidence=0.75)
            )

    authorization = []
    if job.requirements.us_work_auth_required is not None:
        authorization.append(
            FactSignalV2(
                kind="us_work_auth_required",
                value=job.requirements.us_work_auth_required,
                excerpt="structured job requirement",
                confidence=0.9,
            )
        )
    if job.requirements.visa_sponsorship is not None:
        authorization.append(
            FactSignalV2(
                kind="visa_sponsorship",
                value=job.requirements.visa_sponsorship,
                excerpt="structured job requirement",
                confidence=0.9,
            )
        )

    specializations = [
        FactSignalV2(kind="specialization", value=value, excerpt=value, confidence=0.8)
        for value in _SPECIALIZATIONS
        if phrase_in_text(value, combined)
    ]
    workplace_type = _workplace(job, normalized)
    full_description = len(description.strip()) >= 300
    return JobFactsV2(
        full_description_available=full_description,
        title_tokens=tuple(normalize_phrase(job.title).split()),
        country_codes=_country_codes(job, normalized),
        workplace_type=workplace_type,  # type: ignore[arg-type]
        remote_geographies=("US",) if workplace_type == "remote" and "US" in _country_codes(job, normalized) else (),
        compensation=_compensation(job, description),
        level_signals=tuple(level_signals),
        responsibility_signals=tuple(responsibilities),
        capability_requirements=tuple(capabilities),
        domain_signals=tuple(domains),
        authorization_signals=tuple(authorization),
        specialization_signals=tuple(specializations),
        field_confidence={
            "role_responsibilities": 0.8 if responsibilities else (0.5 if job.title else 0.0),
            "experience_level": 0.8 if level_signals else 0.0,
            "requirements_capabilities": 0.8 if capabilities else 0.0,
            "posting_provenance": 0.8 if job.provenance else 0.4,
            "employer": 0.0,
        },
    )


def _gate(
    gate_id: str,
    status: GateStatus,
    code: str,
    message: str,
    evidence: tuple[str, ...] = (),
    confidence: float = 0.0,
) -> GateResultV2:
    return GateResultV2(
        gate_id=gate_id,
        status=status,
        reason_code=code,
        message=message,
        job_evidence=evidence,
        confidence=confidence,
    )


def evaluate_global_eligibility(
    job: RawJob, facts: JobFactsV2, resolved: ResolvedTargetProfile
) -> tuple[GateResultV2, ...]:
    """Apply explicit global/target constraints without positive unknowns."""

    candidate = resolved.candidate
    target = resolved.target
    combined = f"{job.title}\n{job.description or ''}"
    gates: list[GateResultV2] = []

    clearance = _ACTIVE_CLEARANCE_RE.search(combined)
    if clearance and target.constraints.active_clearance == "reject":
        gates.append(
            _gate(
                "security_clearance",
                "fail",
                "active_clearance_required",
                "Role explicitly requires an active security clearance",
                (_excerpt(combined, clearance.start(), clearance.end()),),
                0.95,
            )
        )
    else:
        gates.append(
            _gate(
                "security_clearance",
                "pass",
                "no_active_clearance_requirement",
                "No active-clearance requirement detected",
                confidence=0.8,
            )
        )

    auth = candidate.identity.work_authorization
    requires_us = job.requirements.us_work_auth_required
    sponsorship = job.requirements.visa_sponsorship
    if requires_us is True and auth.status not in {"citizen", "permanent_resident", "ead"}:
        gates.append(
            _gate(
                "work_authorization",
                "fail",
                "us_work_authorization_incompatible",
                "Posting requires US work authorization the candidate does not hold",
                confidence=0.9,
            )
        )
    elif sponsorship is False and auth.sponsorship_needed:
        gates.append(
            _gate(
                "work_authorization",
                "fail",
                "sponsorship_unavailable",
                "Posting does not sponsor and candidate requires sponsorship",
                confidence=0.9,
            )
        )
    elif requires_us is None and sponsorship is None:
        gates.append(
            _gate(
                "work_authorization",
                "unknown",
                "authorization_not_stated",
                "Posting does not state work-authorization policy",
                confidence=0.0,
            )
        )
    else:
        gates.append(
            _gate(
                "work_authorization",
                "pass",
                "authorization_compatible",
                "Structured work authorization is compatible",
                confidence=0.95,
            )
        )

    allowed_types = set(target.constraints.employment_types)
    if job.employment_type == "unknown":
        gates.append(
            _gate(
                "employment_type",
                "unknown",
                "employment_type_unknown",
                "Employment type is not stated",
                confidence=0.0,
            )
        )
    elif job.employment_type not in allowed_types:
        gates.append(
            _gate(
                "employment_type",
                "fail",
                "employment_type_not_allowed",
                f"{job.employment_type} is outside target policy",
                (job.employment_type,),
                0.95,
            )
        )
    else:
        gates.append(
            _gate(
                "employment_type",
                "pass",
                "employment_type_allowed",
                f"{job.employment_type} is allowed",
                (job.employment_type,),
                0.95,
            )
        )

    if job.source == "linkedin":
        gates.append(
            _gate(
                "location",
                "pass",
                "linkedin_server_geofilter",
                "LinkedIn-only server-side geography exception",
                confidence=0.7,
            )
        )
    elif not job.location:
        gates.append(
            _gate(
                "location",
                "unknown",
                "location_unknown",
                "Posting location is missing",
                confidence=0.0,
            )
        )
    else:
        from src.application.jobs import _matches_locations

        if facts.workplace_type == "remote" and candidate.preferences.remote_us_allowed:
            if "US" in facts.country_codes:
                gates.append(
                    _gate(
                        "location",
                        "pass",
                        "remote_us_allowed",
                        "Posting explicitly permits US-remote work",
                        (job.location,),
                        0.95,
                    )
                )
            elif _remote_location_is_bare(job.location):
                gates.append(
                    _gate(
                        "location",
                        "unknown",
                        "remote_geography_unknown",
                        "Remote posting does not state that US candidates are eligible",
                        (job.location,),
                        0.4,
                    )
                )
            else:
                gates.append(
                    _gate(
                        "location",
                        "fail",
                        "remote_outside_us",
                        "Remote posting does not list a US hiring geography",
                        (job.location,),
                        0.9,
                    )
                )
            matched = None
        elif candidate.preferences.onsite_hybrid_us_allowed and "US" in facts.country_codes:
            gates.append(
                _gate(
                    "location",
                    "pass",
                    "onsite_hybrid_us_allowed",
                    "Posting is explicitly US-based and nationwide US roles are allowed",
                    (job.location,),
                    0.95,
                )
            )
            matched = None
        else:
            location_candidates = candidate.preferences.onsite_hybrid_locations
            matched = _matches_locations(job.location, location_candidates)
        if matched is not None:
            gates.append(
                _gate(
                    "location",
                    "pass" if matched else "fail",
                    "location_allowed" if matched else "location_outside_policy",
                    "Location matches candidate policy" if matched else "Location is outside candidate policy",
                    (job.location,),
                    0.9,
                )
            )

    if not job.application_url:
        gates.append(
            _gate(
                "application_url",
                "fail",
                "missing_application_url",
                "No usable posting/application URL is available",
                confidence=0.95,
            )
        )
    else:
        gates.append(
            _gate(
                "application_url",
                "pass",
                "application_url_available",
                "Application URL is available",
                (job.application_url,),
                0.8,
            )
        )

    expired = job.expires_at is not None and job.expires_at <= datetime.now(UTC)
    status = normalize_phrase(str((job.raw_data or {}).get("status") or ""))
    closed = expired or status in {"closed", "expired", "archived"}
    gates.append(
        _gate(
            "posting_state",
            "fail" if closed else "pass",
            "posting_closed" if closed else "posting_open_or_unknown",
            "Posting is closed or expired" if closed else "Posting is not known closed",
            confidence=0.9 if closed else 0.5,
        )
    )
    return tuple(gates)


def aggregate_gate_status(gates: tuple[GateResultV2, ...]) -> GateStatus:
    if any(gate.status == "fail" for gate in gates):
        return "fail"
    if any(gate.status == "unknown" for gate in gates):
        return "unknown"
    return "pass"


__all__ = [
    "GateResultV2",
    "JobFactsV2",
    "PARSER_VERSION",
    "aggregate_gate_status",
    "evaluate_global_eligibility",
    "extract_job_facts",
]
