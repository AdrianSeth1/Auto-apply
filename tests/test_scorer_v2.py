from __future__ import annotations

from src.intake.schema import JobRequirements, RawJob
from src.jobs.employers import assess_employer
from src.jobs.quality import assess_posting
from src.matching.profile_v2 import load_resolved_target
from src.matching.scorer_v2 import evaluate_job_target


def _implementation_job(**overrides) -> RawJob:
    payload = {
        "source": "greenhouse",
        "source_id": "score-fixture",
        "company": "Useful SaaS",
        "title": "Implementation Specialist",
        "location": "Remote, United States",
        "employment_type": "fulltime",
        "description": (
            "Partner with customers on requirements gathering, configure implementation "
            "workflows, lead onboarding and go-live, provide training, and write "
            "documentation. Candidates should have 2+ years of experience. Must be "
            "authorized to work in the United States; sponsorship is not available. "
            "This is a B2B SaaS customer onboarding role."
        ),
        "requirements": JobRequirements(
            must_have_skills=["implementation", "requirements gathering"],
            responsibilities=["configure customer workflows", "lead onboarding"],
            experience_years_min=2,
            us_work_auth_required=True,
            visa_sponsorship=False,
            remote_ok=True,
        ),
        "application_url": "https://boards.greenhouse.io/useful/jobs/1",
    }
    payload.update(overrides)
    return RawJob(**payload)


def test_component_formulas_recompute_exactly() -> None:
    result = evaluate_job_target(
        _implementation_job(), load_resolved_target("saas-implementation")
    )
    score = result.component_scores
    expected_story = 0.50 * score["role"] + 0.35 * score["evidence"] + 0.15 * score["domain"]
    expected_candidacy = (
        0.45 * score["level"]
        + 0.30 * score["evidence"]
        + 0.15 * score["domain"]
        + 0.10 * score["attainability"]
    )
    expected_review = (
        0.40 * expected_story
        + 0.40 * expected_candidacy
        + 0.12 * score["preference"]
        + 0.08 * score["posting_trust"]
    )
    assert result.story_fit == round(expected_story, 2)
    assert result.candidacy_index == round(expected_candidacy, 2)
    assert result.review_index == round(expected_review, 2)
    assert result.tier in {"A", "B"}
    assert result.strengths
    assert not result.gaps


def test_wrong_software_role_cannot_be_rescued_by_generic_ai_vocabulary() -> None:
    job = _implementation_job(
        title="Senior Backend Software Engineer",
        description=(
            "Build production backend services and distributed machine learning "
            "infrastructure. Own APIs, Kubernetes, and model-serving reliability. "
            "Requires 7+ years of experience."
        ),
        requirements=JobRequirements(
            must_have_skills=["Python", "Kubernetes"],
            experience_years_min=7,
            domain="machine_learning",
        ),
    )
    result = evaluate_job_target(job, load_resolved_target("ai-implementation"))
    assert result.tier == "D"
    assert result.component_scores["role"] < 60
    assert result.component_scores["level"] <= 25
    assert any(gap.reason_code == "low_role" for gap in result.gaps)


def test_missing_capabilities_and_years_do_not_reach_viable_tier() -> None:
    job = _implementation_job(
        title="Implementation Specialist",
        description="Help customers succeed with our product.",
        requirements=JobRequirements(remote_ok=True),
    )
    result = evaluate_job_target(job, load_resolved_target("saas-implementation"))
    assert result.tier in {"C", "D"}
    assert "capability_requirements" in result.missing_critical_facts
    assert "required_years" in result.missing_critical_facts
    assert result.component_scores["evidence"] < 50


def test_high_selectivity_without_early_career_evidence_is_stretch_capped() -> None:
    job = _implementation_job(company="Stripe")
    result = evaluate_job_target(job, load_resolved_target("saas-implementation"))
    assert result.tier == "C"
    assert any(gap.reason_code == "high_selectivity_low_odds" for gap in result.gaps)


def test_startup_status_does_not_change_story_fit_or_candidacy() -> None:
    established = _implementation_job(source_id="established")
    startup = _implementation_job(
        source_id="startup", raw_data={"is_startup": True, "funding_stage": "series a"}
    )
    resolved = load_resolved_target("saas-implementation")
    left = evaluate_job_target(established, resolved)
    right = evaluate_job_target(startup, resolved)
    assert right.employer_assessment.lifecycle == "startup"
    assert left.story_fit == right.story_fit
    assert left.candidacy_index == right.candidacy_index


def test_source_transport_has_no_blanket_fit_modifier() -> None:
    direct = _implementation_job(source="greenhouse", source_id="direct")
    aggregator = _implementation_job(
        source="adzuna",
        source_id="aggregator",
        raw_data={"full_jd_recovered": True},
    )
    resolved = load_resolved_target("saas-implementation")
    left = evaluate_job_target(direct, resolved)
    right = evaluate_job_target(aggregator, resolved)
    assert left.component_scores == right.component_scores
    assert left.tier == right.tier


def test_feedback_is_bounded_and_cannot_promote_missing_fact_candidate() -> None:
    job = _implementation_job(
        title="Implementation Specialist",
        description="Help customers succeed with our product.",
        requirements=JobRequirements(remote_ok=True),
    )
    resolved = load_resolved_target("saas-implementation")
    baseline = evaluate_job_target(job, resolved)
    boosted = evaluate_job_target(job, resolved, feedback_value=50)
    assert boosted.feedback_adjustment.value == 5
    assert boosted.adjusted_review_index == min(100, round(baseline.review_index + 5, 2))
    assert boosted.tier == baseline.tier


def test_employer_and_posting_dimensions_are_independent() -> None:
    job = _implementation_job(company="Acme Consulting")
    employer = assess_employer(job)
    posting = assess_posting(job, employer_confidence=employer.confidence)
    assert employer.business_model == "consultancy_professional_services"
    assert employer.employment_relationship == "direct_employer"
    assert posting.application_target_kind == "direct_ats"
    assert posting.trust_score > 50


def test_sr_abbreviation_and_explicit_seniority_cannot_reach_ab() -> None:
    job = _implementation_job(
        title="Sr. Implementation Consultant",
        seniority="senior",
    )
    result = evaluate_job_target(job, load_resolved_target("saas-implementation"))
    assert result.component_scores["level"] <= 25
    assert result.tier in {"C", "D"}


def test_explicit_venture_language_classifies_startup_without_fit_bonus() -> None:
    job = _implementation_job(
        description=_implementation_job().description
        + " We are a venture-backed startup that raised a Series B round."
    )
    assert assess_employer(job).lifecycle == "startup"


def test_explicit_unsupported_specialization_cannot_reach_ab() -> None:
    job = _implementation_job(
        title="Clinical Implementation Specialist",
        description=_implementation_job().description
        + " This is a clinical implementation role for hospital workflows.",
    )
    result = evaluate_job_target(job, load_resolved_target("saas-implementation"))
    assert result.component_scores["domain"] < 30
    assert result.tier in {"C", "D"}


def test_aggregator_snippet_cannot_reach_ab_without_full_jd_recovery() -> None:
    job = _implementation_job(
        source="adzuna",
        description=(
            "Configure customer workflows, lead onboarding, training, go-live, "
            "requirements gathering, and implementation. B2B SaaS. " * 5
        )[:500],
        raw_data={"description_completeness": "snippet"},
    )
    result = evaluate_job_target(job, load_resolved_target("saas-implementation"))
    assert result.posting_assessment.description_completeness == "snippet"
    assert result.tier in {"C", "D"}


def test_normalized_responsibility_synonyms_improve_role_recall_without_title_floor() -> None:
    job = _implementation_job(
        title="Customer Engineer",
        description=(
            "Run discovery calls, demonstrate the platform, and lead solution "
            "configuration for B2B SaaS customers. Candidates need 2+ years of "
            "experience and must be authorized to work in the United States."
        ),
        requirements=JobRequirements(
            must_have_skills=["discovery", "demo", "solution design"],
            experience_years_min=2,
            remote_ok=True,
        ),
    )
    result = evaluate_job_target(job, load_resolved_target("associate-solutions-engineering"))
    assert result.route_tier == "stretch"
    assert result.component_scores["role"] >= 60


def test_supply_chain_operations_is_not_routed_as_revenue_operations() -> None:
    job = _implementation_job(
        title="Supply Chain Operations Analyst",
        description=(
            "Own the purchase order lifecycle, streamline procurement workflows, "
            "build supply chain dashboards, and manage hardware vendors. Candidates "
            "need 3+ years of procurement or logistics experience."
        ),
        requirements=JobRequirements(
            must_have_skills=["analytics", "dashboards", "process improvement"],
            experience_years_min=3,
            remote_ok=True,
        ),
    )
    result = evaluate_job_target(job, load_resolved_target("revenue-operations-analyst"))
    assert result.route_tier == "excluded"
    assert result.tier == "D"


def test_preferred_metro_ranks_above_other_eligible_us_city() -> None:
    resolved = load_resolved_target("saas-implementation")
    preferred = evaluate_job_target(
        _implementation_job(source_id="preferred-metro", location="San Francisco, CA"),
        resolved,
    )
    nationwide = evaluate_job_target(
        _implementation_job(source_id="nationwide-us", location="Chicago, IL"),
        resolved,
    )
    assert next(g for g in nationwide.gate_results if g.gate_id == "location").status == "pass"
    assert preferred.component_scores["preference"] > nationwide.component_scores["preference"]
