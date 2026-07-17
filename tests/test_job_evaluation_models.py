from __future__ import annotations

from src.core.database import Base
from src.matching.evaluation_store import reason_rows_for_evaluation
from src.matching.profile_v2 import load_resolved_target
from src.matching.scorer_v2 import evaluate_job_target
from src.intake.schema import JobRequirements, RawJob


def test_v2_tables_and_links_exist_in_metadata() -> None:
    required = {
        "source_endpoints",
        "source_endpoint_runs",
        "source_query_arms",
        "source_query_runs",
        "employers",
        "employer_assessments",
        "job_quality_assessments",
        "discovery_runs",
        "job_target_evaluations",
        "job_evaluation_reasons",
        "portfolio_runs",
        "portfolio_decisions",
        "review_feedback",
        "evaluation_sets",
        "evaluation_items",
    }
    assert required <= set(Base.metadata.tables)
    assert "evaluation_id" in Base.metadata.tables["review_queue"].columns
    assert "portfolio_decision_id" in Base.metadata.tables["review_queue"].columns
    assert "evaluation_id" in Base.metadata.tables["applications"].columns
    assert "journey_key" in Base.metadata.tables["funnel_events"].columns


def test_every_evaluation_has_terminal_stage_reason_rows() -> None:
    job = RawJob(
        source="greenhouse",
        source_id="ledger",
        company="Ledger Co",
        title="Implementation Specialist",
        location="Remote, United States",
        employment_type="fulltime",
        description=(
            "Gather requirements, configure implementation workflows, and lead onboarding. "
            "Requires 2+ years of experience. B2B SaaS."
        ),
        requirements=JobRequirements(
            must_have_skills=["implementation", "requirements gathering"],
            experience_years_min=2,
            remote_ok=True,
        ),
        application_url="https://boards.greenhouse.io/ledger/jobs/1",
    )
    result = evaluate_job_target(job, load_resolved_target("saas-implementation"))
    reasons = reason_rows_for_evaluation(result)
    stages = {row["stage"] for row in reasons}
    assert {"global_eligibility", "target_routing", "target_candidacy"} <= stages
    assert all(row["reason_code"] and row["decision"] for row in reasons)
