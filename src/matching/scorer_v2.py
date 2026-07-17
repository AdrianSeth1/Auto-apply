"""Deterministic, component-floor Job Pool V2 evaluator."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.intake.schema import RawJob
from src.jobs.employers import EmployerAssessmentV2, assess_employer
from src.jobs.quality import PostingQualityAssessmentV2, assess_posting
from src.matching.explanations_v2 import (
    EvaluationReasonV2,
    component_gaps,
    component_strengths,
)
from src.matching.job_facts import (
    GateResultV2,
    JobFactsV2,
    aggregate_gate_status,
    evaluate_global_eligibility,
    extract_job_facts,
)
from src.matching.target_schema import ResolvedTargetProfile, normalize_phrase, phrase_in_text

SCORER_VERSION = "job-pool-v2.5"
Tier = Literal["A", "B", "C", "D", "unresolved"]

_EVIDENCE_BASE = {
    "quantified_professional": 1.0,
    "direct_professional": 0.85,
    "adopted_external_project": 0.8,
    "production_like_project": 0.7,
    "adjacent_professional": 0.6,
    "coursework": 0.35,
    "plausible_narrative": 0.2,
}
_VERIFICATION = {
    "documented": 1.0,
    "self_reported": 0.9,
    "plausible": 0.65,
    "needs_review": 0.25,
}
_ADJACENT_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "cap_stakeholder_translation": ("cap_technical_translation", "cap_communication"),
    "cap_demos": ("cap_presentations", "cap_public_speaking"),
    "cap_process_improvement": ("cap_process_design", "cap_operations"),
    "cap_project_delivery": ("cap_delivery", "cap_implementation"),
    "cap_customer_success": ("cap_onboarding", "cap_adoption", "cap_client_facing"),
    "cap_discovery": ("cap_requirements", "cap_scoping", "cap_analysis"),
    "cap_analytics": ("cap_analysis", "cap_analytical", "cap_dashboards"),
}

# Normalized JobFacts responsibility categories that are strong equivalents
# for each target's configured phrases. This recovers ordinary JD wording
# (for example "needs analysis" for "workflow discovery") without fuzzy
# title floors or lowering any A/B threshold.
_TARGET_RESPONSIBILITY_FACTS: dict[str, frozenset[str]] = {
    "ai-implementation": frozenset(
        {
            "workflow_discovery",
            "configuration",
            "onboarding",
            "project_delivery",
            "customer_enablement",
            "technical_discovery",
            "demonstrations",
        }
    ),
    "saas-implementation": frozenset(
        {
            "workflow_discovery",
            "configuration",
            "onboarding",
            "project_delivery",
            "customer_enablement",
        }
    ),
    "revenue-operations-analyst": frozenset({"analytics", "process_improvement"}),
    "associate-solutions-engineering": frozenset(
        {"technical_discovery", "demonstrations", "configuration"}
    ),
    "technical-customer-success": frozenset(
        {"onboarding", "customer_enablement", "customer_success", "technical_discovery"}
    ),
}


class FeedbackAdjustmentV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float = Field(default=0.0, ge=-5, le=5)
    priors_used: tuple[dict, ...] = ()


class JobTargetEvaluationV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_id: str
    candidate_version: str
    target_version: str
    pipeline_version: str = "v2"
    parser_version: str
    scorer_version: str = SCORER_VERSION
    taxonomy_versions: tuple[str, str]
    gate_results: tuple[GateResultV2, ...]
    component_scores: dict[str, float]
    component_confidence: dict[str, float]
    story_fit: float
    candidacy_index: float
    review_index: float
    adjusted_review_index: float
    tier: Tier
    confidence: float = Field(ge=0, le=1)
    route_tier: str
    strengths: tuple[EvaluationReasonV2, ...]
    gaps: tuple[EvaluationReasonV2, ...]
    feedback_adjustment: FeedbackAdjustmentV2
    employer_assessment: EmployerAssessmentV2
    posting_assessment: PostingQualityAssessmentV2
    missing_critical_facts: tuple[str, ...] = ()

    def to_persisted_dict(self) -> dict:
        return self.model_dump(mode="json")


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def _title_tier(job: RawJob, resolved: ResolvedTargetProfile) -> tuple[str, float]:
    title = job.title
    if any(phrase_in_text(term, title) for term in resolved.target.role.excluded_title_terms):
        return "excluded", 0.0
    for tier, value in (("core", 1.0), ("adjacent", 0.8), ("stretch", 0.65)):
        if any(phrase_in_text(phrase, title) for phrase in resolved.normalized_title_rules[tier]):
            return tier, value
    description_only = resolved.normalized_title_rules["description_only"]
    if any(phrase_in_text(phrase, title) for phrase in description_only):
        return "description_only", 0.5
    return "unmatched", 0.0


def _responsibility_coverage(
    job: RawJob,
    resolved: ResolvedTargetProfile,
    facts: JobFactsV2 | None = None,
) -> tuple[float, float]:
    description = job.description or ""
    exact_positives = sum(
        phrase_in_text(signal, description)
        for signal in resolved.target.role.responsibility_signals
    )
    facts = facts or extract_job_facts(job)
    normalized_responsibilities = {
        str(signal.value) for signal in facts.responsibility_signals
    }
    fact_positives = len(
        normalized_responsibilities
        & _TARGET_RESPONSIBILITY_FACTS.get(resolved.target.id, frozenset())
    )
    positives = max(exact_positives, fact_positives)
    # Phase S7: candidate-level dislikes (e.g. "door to door") apply across
    # every target, unlike TargetRoleV2.negative_responsibility_signals which
    # is per-target -- merged here rather than duplicated into each target's
    # YAML file.
    negative_signals = (
        *resolved.target.role.negative_responsibility_signals,
        *resolved.candidate.preferences.excluded_role_signals,
    )
    negatives = sum(phrase_in_text(signal, description) for signal in negative_signals)
    denominator = max(1, min(3, len(resolved.target.role.responsibility_signals)))
    positive_score = min(1.0, positives / denominator)
    negative_share = negatives / max(1, positives + negatives)
    return positive_score, negative_share


def _role_score(
    job: RawJob, facts: JobFactsV2, resolved: ResolvedTargetProfile
) -> tuple[float, float, str]:
    route_tier, title = _title_tier(job, resolved)
    work, negative = _responsibility_coverage(job, resolved, facts)
    combined = f"{job.title} {job.description or ''}"
    domain_hits = sum(
        phrase_in_text(signal, combined) for signal in resolved.target.role.domain_signals
    )
    domain = 1.0 if domain_hits else (0.5 if resolved.target.role.domain_signals else 0.0)
    score = 100 * _clamp(0.30 * title + 0.60 * work + 0.10 * domain - 0.50 * negative, 0, 1)
    if negative >= 0.5:
        score = min(score, 35)
    if title and not work:
        score = min(score, 65)
    confidence = min(1.0, 0.45 * bool(title) + 0.45 * min(1.0, work + negative) + 0.10 * bool(domain_hits))
    return score, confidence, route_tier


def _level_score(
    job: RawJob, facts: JobFactsV2, resolved: ResolvedTargetProfile, route_tier: str
) -> tuple[float, float, bool, bool, int | None]:
    years_signal = next(
        (signal for signal in facts.level_signals if signal.kind == "required_years"), None
    )
    years = int(years_signal.value) if years_signal else None
    y = {0: 1.0, 1: 1.0, 2: 1.0, 3: 0.55, 4: 0.3, 5: 0.1, 6: 0.0}.get(
        years, 0.0 if years is not None else 0.5
    )
    title = normalize_phrase(job.title)
    if any(term in title.split() for term in ("intern", "associate", "junior")) or phrase_in_text("new grad", title):
        h = 1.0
    elif any(term in title.split() for term in ("staff", "principal", "director", "head")) or job.seniority == "staff":
        h = 0.05
    elif any(term in title.split() for term in ("senior", "sr", "lead")) or job.seniority == "senior":
        h = 0.2
    elif "manager" in title.split():
        h = 0.35
    elif job.seniority == "mid":
        h = 0.55
    else:
        h = 0.65

    text = normalize_phrase(job.description or "")
    if any(phrase_in_text(term, text) for term in ("lead an organization", "manage managers", "architecture authority")):
        scope = 0.15
    elif any(phrase_in_text(term, text) for term in ("book of business", "renewal quota", "enterprise portfolio")):
        scope = 0.4
    elif any(phrase_in_text(term, text) for term in ("own projects", "lead implementations", "independently manage")):
        scope = 0.8
    else:
        scope = 0.65
    score = 100 * (0.45 * y + 0.30 * h + 0.25 * scope)
    max_years = (
        resolved.target.candidacy.stretch_max_required_years
        if route_tier == "stretch"
        else resolved.target.candidacy.core_max_required_years
    )
    severe_years = years is not None and years > max_years
    severe_title = h <= 0.05
    if severe_years:
        score = min(score, 25)
    explicit_seniority = job.seniority if job.seniority != "unknown" else None
    if explicit_seniority and explicit_seniority not in set(resolved.target.candidacy.allowed_seniority):
        score = min(score, 25)
    if route_tier == "excluded":
        score = 0
    if severe_title:
        score = min(score, 15)
    founding = phrase_in_text("founding", job.title)
    if founding:
        score = min(score, 35)
    confidence = 0.45 * (0.9 if years is not None else 0.0) + 0.30 * (0.8 if h != 0.65 else 0.3) + 0.25 * (0.75 if scope != 0.65 else 0.0)
    return score, confidence, years is None, founding, years


def _candidate_evidence(resolved: ResolvedTargetProfile) -> tuple[dict[str, object], dict[str, object]]:
    bullets = {
        bullet.id: bullet
        for experience in resolved.candidate.experiences
        for bullet in experience.bullets
    }
    bullets.update(
        {
            bullet.id: bullet
            for project in resolved.candidate.projects
            for bullet in project.bullets
        }
    )
    capabilities = {item.id: item for item in resolved.candidate.capabilities}
    return bullets, capabilities


def _capability_support(
    capability_id: str, resolved: ResolvedTargetProfile, bullets: dict[str, object], capabilities: dict[str, object]
) -> tuple[float, tuple[str, ...]]:
    candidate_ids = [capability_id]
    candidate_ids.extend(_ADJACENT_CAPABILITIES.get(capability_id, ()))
    best_score = 0.0
    best_refs: tuple[str, ...] = ()
    for index, candidate_id in enumerate(candidate_ids):
        capability = capabilities.get(candidate_id)
        if capability is None:
            continue
        refs = tuple(getattr(capability, "evidence_refs", ()))
        scores = sorted(
            (
                _EVIDENCE_BASE.get(getattr(bullets.get(ref), "evidence_strength", ""), 0.0)
                * _VERIFICATION.get(getattr(bullets.get(ref), "verification", ""), 0.0)
                for ref in refs
                if ref in bullets
            ),
            reverse=True,
        )
        if not scores:
            continue
        raw = min(1.0, scores[0] + (0.10 * scores[1] if len(scores) > 1 else 0.0))
        transfer = 1.0 if index == 0 else 0.75
        score = raw * transfer
        if score > best_score:
            best_score, best_refs = score, refs
    return best_score, best_refs


def _evidence_score(
    job: RawJob, facts: JobFactsV2, resolved: ResolvedTargetProfile
) -> tuple[float, float, tuple[str, ...], tuple[str, ...], bool]:
    bullets, capabilities = _candidate_evidence(resolved)
    requirements = facts.capability_requirements
    if not requirements:
        work, _ = _responsibility_coverage(job, resolved, facts)
        return 50 * work, 0.3 if work else 0.0, (), (), True

    weights = {"mandatory": 3.0, "core": 2.0, "preferred": 1.0}
    total = 0.0
    supported = 0.0
    evidence_refs: list[str] = []
    missing: list[str] = []
    critical_missing = False
    for requirement in requirements:
        weight = weights[requirement.importance]
        support, refs = _capability_support(
            requirement.capability_id, resolved, bullets, capabilities
        )
        total += weight
        supported += weight * support
        if support:
            evidence_refs.extend(refs)
        else:
            missing.append(requirement.capability_id)
            critical_missing |= requirement.importance == "mandatory"
    score = 100 * supported / total if total else 0.0
    if critical_missing:
        score = min(score, 49)
    confidence = sum(requirement.confidence * weights[requirement.importance] for requirement in requirements) / total
    return score, confidence, tuple(dict.fromkeys(evidence_refs)), tuple(missing), False


def _domain_score(
    job: RawJob, facts: JobFactsV2, resolved: ResolvedTargetProfile
) -> tuple[float, float, bool]:
    combined = f"{job.title} {job.description or ''}"
    if any(
        phrase_in_text(term, combined)
        for term in resolved.target.candidacy.unsupported_specializations
    ):
        return 15.0, 0.9, False
    domain_known = bool(facts.domain_signals)
    if any(phrase_in_text(signal, combined) for signal in resolved.target.role.domain_signals):
        domain = 1.0
        domain_confidence = 0.8
    elif domain_known and any(
        phrase_in_text(term, combined) for term in ("saas", "software", "customer")
    ):
        domain = 0.75
        domain_confidence = 0.65
    elif facts.specialization_signals:
        domain = 0.2
        domain_confidence = 0.8
    else:
        domain = None
        domain_confidence = 0.0

    _, capabilities = _candidate_evidence(resolved)
    if facts.capability_requirements:
        values = []
        for requirement in facts.capability_requirements:
            if requirement.capability_id in capabilities:
                values.append(1.0)
            elif any(
                candidate in capabilities
                for candidate in _ADJACENT_CAPABILITIES.get(requirement.capability_id, ())
            ):
                values.append(0.6)
            else:
                values.append(0.35 if requirement.importance != "mandatory" else 0.0)
        tools = sum(values) / len(values)
        tool_confidence = 0.75
    else:
        tools = None
        tool_confidence = 0.0

    if domain is None and tools is None:
        return 50.0, 0.0, True
    if domain is None:
        return 100 * tools, tool_confidence * 0.7, True  # type: ignore[operator]
    if tools is None:
        return 100 * domain, domain_confidence * 0.7, True
    return 100 * (0.55 * domain + 0.45 * tools), 0.55 * domain_confidence + 0.45 * tool_confidence, False


def _attainability(
    job: RawJob, employer: EmployerAssessmentV2
) -> tuple[float, float, bool]:
    text = f"{job.title} {job.description or ''}"
    score = 65.0
    early = any(
        phrase_in_text(term, text)
        for term in ("early career", "new grad", "entry level", "career changer")
    )
    if early:
        score += 15
    if any(phrase_in_text(term, text) for term in ("nontraditional backgrounds", "project experience welcome")):
        score += 10
    if employer.selectivity_tier in {"high", "exceptional"}:
        score -= 10
    founding = phrase_in_text("founding", job.title) or phrase_in_text("first technical hire", text)
    if founding:
        score -= 25
    if any(phrase_in_text(term, text) for term in ("proven enterprise track record", "deep industry tenure")):
        score -= 15
    return _clamp(score), max(0.5, employer.confidence), early


def _preference(
    job: RawJob,
    facts: JobFactsV2,
    resolved: ResolvedTargetProfile,
    employer: EmployerAssessmentV2,
    gates: tuple[GateResultV2, ...],
) -> tuple[float, float]:
    prefs = resolved.candidate.preferences
    role_interest = resolved.target.priority * 100
    location_gate = next(gate for gate in gates if gate.gate_id == "location")
    if location_gate.status == "pass":
        from src.application.jobs import _matches_locations

        preferred_metro = _matches_locations(job.location, prefs.preferred_locations)
        if preferred_metro:
            geography = 100
        elif facts.workplace_type == "remote" and "US" in facts.country_codes:
            geography = 80
        else:
            geography = 75
    elif location_gate.status == "unknown":
        geography = 50
    else:
        geography = 0
    target_salary = prefs.compensation.preferred_base_min
    if facts.compensation is None or target_salary is None:
        compensation = 50
        compensation_confidence = 0.0
    elif facts.compensation.minimum is not None and facts.compensation.minimum >= target_salary:
        compensation = 100
        compensation_confidence = facts.compensation.confidence
    elif facts.compensation.maximum is not None and facts.compensation.maximum >= target_salary:
        compensation = 70
        compensation_confidence = facts.compensation.confidence
    else:
        compensation = 30
        compensation_confidence = facts.compensation.confidence
    # Phase S7: a named "exciting" company gets a bump within the existing
    # employer_interest component rather than a new weighted term, so the
    # 0.15 weight below and the score's overall calibration are untouched --
    # only what feeds this one component changes.
    if employer.employment_relationship == "staffing_intermediary":
        employer_interest = 0
    elif normalize_phrase(job.company) in {
        normalize_phrase(company) for company in prefs.preferred_companies
    }:
        employer_interest = 100
    else:
        employer_interest = 60
    startup_interest = prefs.startup_interest * 100 if employer.lifecycle == "startup" else 60
    travel = 50
    score = (
        0.25 * role_interest
        + 0.25 * geography
        + 0.20 * compensation
        + 0.15 * employer_interest
        + 0.10 * startup_interest
        + 0.05 * travel
    )
    confidence = (
        0.25 * 1.0
        + 0.25 * location_gate.confidence
        + 0.20 * compensation_confidence
        + 0.15 * employer.confidence
        + 0.10 * (employer.confidence if employer.lifecycle != "unknown" else 0.0)
    )
    return score, min(1.0, confidence)


def _tier(
    scores: dict[str, float],
    story: float,
    candidacy: float,
    review: float,
    confidence: float,
    gates: tuple[GateResultV2, ...],
    *,
    missing_capabilities: bool,
    unknown_years: bool,
    domain_unknown: bool,
    high_selectivity_without_early_signal: bool,
    required_years: int | None,
    route_tier: str,
    posting_incomplete: bool,
) -> Tier:
    if any(gate.status == "fail" for gate in gates):
        return "D"
    role, level, evidence = scores["role"], scores["level"], scores["evidence"]
    if (
        confidence >= 0.70
        and role >= 70
        and evidence >= 60
        and level >= 60
        and story >= 68
        and candidacy >= 60
        and review >= 68
        and not domain_unknown
    ):
        tier: Tier = "A"
    elif (
        confidence >= 0.55
        and role >= 60
        and evidence >= 50
        and level >= 50
        and story >= 60
        and candidacy >= 52
        and review >= 58
    ):
        tier = "B"
    elif (
        confidence >= 0.45
        and role >= 58
        and evidence >= 40
        and level >= 30
        and story >= 55
        and candidacy >= 40
        and review >= 50
    ):
        tier = "C"
    else:
        tier = "D"
    if tier in {"A", "B"} and ((missing_capabilities and unknown_years) or high_selectivity_without_early_signal):
        tier = "C"
    if tier == "A" and required_years is not None and required_years >= 3:
        tier = "B"
    if tier in {"A", "B"} and required_years is not None and required_years >= 4:
        tier = "C"
    if tier in {"A", "B"} and route_tier in {"excluded", "unmatched", "description_only"}:
        tier = "C"
    if tier in {"A", "B"} and scores["domain"] < 30:
        tier = "C"
    if tier in {"A", "B"} and posting_incomplete:
        tier = "C"
    return tier


def evaluate_job_target(
    job: RawJob,
    resolved: ResolvedTargetProfile,
    *,
    facts: JobFactsV2 | None = None,
    employer: EmployerAssessmentV2 | None = None,
    posting: PostingQualityAssessmentV2 | None = None,
    gates: tuple[GateResultV2, ...] | None = None,
    feedback_value: float = 0.0,
    feedback_priors: tuple[dict, ...] = (),
    pipeline_version: str = "v2",
) -> JobTargetEvaluationV2:
    facts = facts or extract_job_facts(job)
    employer = employer or assess_employer(job)
    posting = posting or assess_posting(job, employer_confidence=employer.confidence)
    gates = gates or evaluate_global_eligibility(job, facts, resolved)
    role, role_conf, route_tier = _role_score(job, facts, resolved)
    level, level_conf, unknown_years, founding, required_years = _level_score(
        job, facts, resolved, route_tier
    )
    evidence, evidence_conf, evidence_refs, missing_caps, missing_capability_facts = _evidence_score(
        job, facts, resolved
    )
    domain, domain_conf, domain_unknown = _domain_score(job, facts, resolved)
    attainability, attainability_conf, early_signal = _attainability(job, employer)
    preference, preference_conf = _preference(job, facts, resolved, employer, gates)
    scores = {
        "role": round(role, 2),
        "level": round(level, 2),
        "evidence": round(evidence, 2),
        "domain": round(domain, 2),
        "attainability": round(attainability, 2),
        "preference": round(preference, 2),
        "posting_trust": round(posting.trust_score, 2),
    }
    confidences = {
        "role": round(role_conf, 4),
        "level": round(level_conf, 4),
        "evidence": round(evidence_conf, 4),
        "domain": round(domain_conf, 4),
        "attainability": round(attainability_conf, 4),
        "preference": round(preference_conf, 4),
        "posting_trust": round(posting.confidence, 4),
    }
    story = 0.50 * role + 0.35 * evidence + 0.15 * domain
    candidacy = 0.45 * level + 0.30 * evidence + 0.15 * domain + 0.10 * attainability
    review = 0.40 * story + 0.40 * candidacy + 0.12 * preference + 0.08 * posting.trust_score
    bounded_feedback = _clamp(feedback_value, -5, 5)
    adjusted = _clamp(review + bounded_feedback)

    field_confidence = facts.field_confidence
    confidence = (
        0.30 * field_confidence.get("role_responsibilities", 0.0)
        + 0.25 * field_confidence.get("experience_level", 0.0)
        + 0.20 * field_confidence.get("requirements_capabilities", 0.0)
        + 0.15 * employer.confidence
        + 0.10 * posting.confidence
    )
    high_selectivity_without_early = (
        employer.selectivity_tier in {"high", "exceptional"} and not early_signal
    )
    tier = _tier(
        scores,
        story,
        candidacy,
        review,
        confidence,
        gates,
        missing_capabilities=missing_capability_facts,
        unknown_years=unknown_years,
        domain_unknown=domain_unknown,
        high_selectivity_without_early_signal=high_selectivity_without_early,
        required_years=required_years,
        route_tier=route_tier,
        posting_incomplete=posting.description_completeness in {"snippet", "missing"},
    )
    gate_failures = tuple(
        (gate.reason_code, gate.message) for gate in gates if gate.status == "fail"
    )
    strengths = component_strengths(
        scores, title=job.title, evidence_refs=evidence_refs
    )
    gaps = list(
        component_gaps(
            scores,
            missing_capabilities=missing_caps,
            gate_failures=gate_failures,
        )
    )
    if high_selectivity_without_early:
        gaps.append(
            EvaluationReasonV2(
                reason_code="high_selectivity_low_odds",
                message="High-selectivity employer without explicit early-career evidence; capped at stretch",
                severity="warning",
            )
        )
    if founding:
        gaps.append(
            EvaluationReasonV2(
                reason_code="founding_scope",
                message="Founding scope is materially above the candidate's documented tenure",
                job_excerpt=job.title,
                severity="warning",
            )
        )
    missing_facts = []
    if unknown_years:
        missing_facts.append("required_years")
    if missing_capability_facts:
        missing_facts.append("capability_requirements")
    if domain_unknown:
        missing_facts.append("domain_or_tools")
    if aggregate_gate_status(gates) == "unknown":
        missing_facts.append("eligibility_fact")
    return JobTargetEvaluationV2(
        target_id=resolved.target.id,
        candidate_version=resolved.candidate_version,
        target_version=resolved.target_version,
        pipeline_version=pipeline_version,
        parser_version=facts.parser_version,
        taxonomy_versions=(
            resolved.role_taxonomy_version,
            resolved.capability_taxonomy_version,
        ),
        gate_results=gates,
        component_scores=scores,
        component_confidence=confidences,
        story_fit=round(story, 2),
        candidacy_index=round(candidacy, 2),
        review_index=round(review, 2),
        adjusted_review_index=round(adjusted, 2),
        tier=tier,
        confidence=round(confidence, 4),
        route_tier=route_tier,
        strengths=strengths,
        gaps=tuple(gaps),
        feedback_adjustment=FeedbackAdjustmentV2(
            value=bounded_feedback, priors_used=feedback_priors
        ),
        employer_assessment=employer,
        posting_assessment=posting,
        missing_critical_facts=tuple(dict.fromkeys(missing_facts)),
    )


__all__ = [
    "FeedbackAdjustmentV2",
    "JobTargetEvaluationV2",
    "SCORER_VERSION",
    "evaluate_job_target",
]
