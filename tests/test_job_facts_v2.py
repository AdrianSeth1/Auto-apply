from __future__ import annotations

from src.intake.schema import JobRequirements, RawJob
from src.matching.job_facts import (
    aggregate_gate_status,
    evaluate_global_eligibility,
    extract_job_facts,
)
from src.matching.profile_v2 import load_resolved_target


def _job(**overrides) -> RawJob:
    payload = {
        "source": "greenhouse",
        "source_id": "v2-fixture",
        "company": "Useful SaaS",
        "title": "Implementation Specialist",
        "location": "Remote, United States",
        "employment_type": "fulltime",
        "description": (
            "Partner with customers on requirements gathering, configure their "
            "workflows, lead onboarding and go-live, and write implementation guides. "
            "Candidates should have 2+ years of experience. Must be authorized to work "
            "in the United States; sponsorship is not available."
        ),
        "requirements": JobRequirements(
            must_have_skills=["implementation", "requirements gathering"],
            responsibilities=["configure customer workflows", "lead onboarding"],
            experience_years_min=2,
            us_work_auth_required=True,
            visa_sponsorship=False,
            remote_ok=True,
        ),
        "application_url": "https://example.com/apply/1",
    }
    payload.update(overrides)
    return RawJob(**payload)


def test_extracts_customer_facing_facts_with_evidence() -> None:
    facts = extract_job_facts(_job())
    responsibility_ids = {str(item.value) for item in facts.responsibility_signals}
    capability_ids = {item.capability_id for item in facts.capability_requirements}
    assert "workflow_discovery" in responsibility_ids
    assert "configuration" in responsibility_ids
    assert "onboarding" in responsibility_ids
    assert {"cap_implementation", "cap_discovery", "cap_onboarding"} <= capability_ids
    assert facts.level_signals[0].excerpt
    assert facts.field_confidence["requirements_capabilities"] > 0


def test_permanent_resident_passes_explicit_us_authorization_requirement() -> None:
    resolved = load_resolved_target("saas-implementation")
    job = _job()
    gates = evaluate_global_eligibility(job, extract_job_facts(job), resolved)
    authorization = next(gate for gate in gates if gate.gate_id == "work_authorization")
    assert authorization.status == "pass"
    assert authorization.reason_code == "authorization_compatible"
    assert aggregate_gate_status(gates) == "pass"


def test_unknown_fields_are_unknown_not_positive_passes() -> None:
    resolved = load_resolved_target("saas-implementation")
    job = _job(
        location=None,
        employment_type="unknown",
        description="Short role summary.",
        requirements=JobRequirements(),
    )
    facts = extract_job_facts(job)
    gates = evaluate_global_eligibility(job, facts, resolved)
    by_id = {gate.gate_id: gate for gate in gates}
    assert by_id["location"].status == "unknown"
    assert by_id["employment_type"].status == "unknown"
    assert by_id["work_authorization"].status == "unknown"
    assert not facts.capability_requirements
    assert facts.field_confidence["requirements_capabilities"] == 0
    assert aggregate_gate_status(gates) == "unknown"


def test_non_linkedin_wrong_region_fails_but_linkedin_uses_only_its_exception() -> None:
    resolved = load_resolved_target("saas-implementation")
    ats = _job(location="Berlin, Germany")
    ats_gate = next(
        gate
        for gate in evaluate_global_eligibility(ats, extract_job_facts(ats), resolved)
        if gate.gate_id == "location"
    )
    assert ats_gate.status == "fail"

    linkedin = _job(source="linkedin", location="Berlin, Germany")
    linkedin_gate = next(
        gate
        for gate in evaluate_global_eligibility(
            linkedin, extract_job_facts(linkedin), resolved
        )
        if gate.gate_id == "location"
    )
    assert linkedin_gate.status == "pass"
    assert linkedin_gate.reason_code == "linkedin_server_geofilter"


def test_remote_foreign_location_fails_even_when_description_mentions_us() -> None:
    resolved = load_resolved_target("saas-implementation")
    job = _job(
        location="Remote - India",
        description=_job().description + " Our company also has employees in the United States.",
    )
    gate = next(
        value
        for value in evaluate_global_eligibility(job, extract_job_facts(job), resolved)
        if value.gate_id == "location"
    )
    assert gate.status == "fail"
    assert gate.reason_code == "remote_outside_us"


def test_bare_remote_location_is_unknown_not_a_positive_us_match() -> None:
    resolved = load_resolved_target("saas-implementation")
    job = _job(location="Remote")
    gate = next(
        value
        for value in evaluate_global_eligibility(job, extract_job_facts(job), resolved)
        if value.gate_id == "location"
    )
    assert gate.status == "unknown"
    assert gate.reason_code == "remote_geography_unknown"


def test_any_explicit_us_onsite_location_is_eligible() -> None:
    resolved = load_resolved_target("saas-implementation")
    job = _job(
        location="Chicago, IL",
        description=_job().description + " This position is based in our Chicago office.",
        requirements=JobRequirements(remote_ok=False),
    )
    gate = next(
        value
        for value in evaluate_global_eligibility(job, extract_job_facts(job), resolved)
        if value.gate_id == "location"
    )
    assert gate.status == "pass"
    assert gate.reason_code == "onsite_hybrid_us_allowed"


def test_foreign_onsite_location_remains_outside_policy() -> None:
    resolved = load_resolved_target("saas-implementation")
    job = _job(
        location="Toronto, ON, Canada",
        requirements=JobRequirements(remote_ok=False),
    )
    gate = next(
        value
        for value in evaluate_global_eligibility(job, extract_job_facts(job), resolved)
        if value.gate_id == "location"
    )
    assert gate.status == "fail"
    assert gate.reason_code == "location_outside_policy"


def test_active_clearance_and_missing_url_are_explicit_failures() -> None:
    resolved = load_resolved_target("ai-implementation")
    job = _job(
        title="AI Solutions Engineer - Active Secret Clearance",
        application_url=None,
    )
    gates = evaluate_global_eligibility(job, extract_job_facts(job), resolved)
    reasons = {gate.reason_code for gate in gates if gate.status == "fail"}
    assert "active_clearance_required" in reasons
    assert "missing_application_url" in reasons
    assert aggregate_gate_status(gates) == "fail"


def test_short_ai_token_does_not_create_ai_capability_from_gainsight() -> None:
    job = _job(
        title="Gainsight Administrator",
        description="Administer Gainsight and customer health workflows.",
        requirements=JobRequirements(),
    )
    facts = extract_job_facts(job)
    assert "cap_ai" not in {
        requirement.capability_id for requirement in facts.capability_requirements
    }


def test_company_growth_years_do_not_override_required_experience() -> None:
    job = _job(
        description=(
            "We tripled revenue each year for 3 years running. "
            "Required: 2+ years of enterprise SaaS implementation experience."
        )
    )
    facts = extract_job_facts(job)
    years = next(signal for signal in facts.level_signals if signal.kind == "required_years")
    assert years.value == 2


def test_named_us_office_is_eligible_under_nationwide_policy() -> None:
    resolved = load_resolved_target("saas-implementation")
    job = _job(
        location="USA - Nebraska - Lincoln Office",
        description=(
            "This role requires three days in the office and commuting distance "
            "from Lincoln. Our benefits include remote work options."
        ),
        requirements=JobRequirements(remote_ok=True),
    )
    facts = extract_job_facts(job)
    gate = next(
        value
        for value in evaluate_global_eligibility(job, facts, resolved)
        if value.gate_id == "location"
    )
    assert facts.workplace_type in {"onsite", "hybrid"}
    assert gate.status == "pass"
    assert gate.reason_code == "onsite_hybrid_us_allowed"
