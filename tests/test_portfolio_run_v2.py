from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select

from src.core.database import get_session_factory
from src.core.models import (
    DiscoveryRun,
    DiscoveryRunEvaluation,
    JobEvaluationReason,
    JobPosting,
    JobSnapshot,
    JobTargetEvaluation,
    PortfolioDecision,
    PortfolioRun,
    ReviewQueueEntry,
    SourceQueryArm,
    SourceQueryRun,
)
from src.intake.schema import JobRequirements, RawJob
from src.jobs.employers import assess_employer
from src.jobs.quality import assess_posting
from src.matching.job_facts import extract_job_facts
from src.orchestration.portfolio_run import (
    build_acquisition_request,
    raw_job_from_payload,
    run_portfolio_v2,
)


def _job(source_id: str) -> RawJob:
    return RawJob(
        source="greenhouse",
        source_id=source_id,
        company=f"V2 Test Company {source_id}",
        title="Implementation Specialist",
        location="Remote, United States",
        employment_type="fulltime",
        description=(
            "Partner with customers on requirements gathering, configure implementation "
            "workflows, lead onboarding and go-live, provide training, and write "
            "documentation. Candidates need 2+ years of experience. B2B SaaS. "
            "Own implementation plans from kickoff through launch, collaborate with "
            "customer stakeholders, document decisions, and help teams adopt the "
            "configured workflow after go-live."
        ),
        requirements=JobRequirements(
            must_have_skills=["implementation", "requirements gathering"],
            experience_years_min=2,
            remote_ok=True,
        ),
        application_url=f"https://boards.greenhouse.io/v2test/jobs/{source_id}",
    )


def test_raw_payload_round_trip_preserves_requirements() -> None:
    job = _job("round-trip")
    payload = job.model_dump(mode="json")
    payload["match_score"] = 0.75
    round_trip = raw_job_from_payload(payload)
    assert round_trip.source_id == job.source_id
    assert round_trip.requirements.experience_years_min == 2
    assert round_trip.requirements.must_have_skills


def test_acquisition_request_is_one_unfiltered_deterministic_broad_funnel() -> None:
    from src.intake.query_scheduler import QueryArmV2
    from src.matching.profile_v2 import load_candidate, load_targets, resolve_target

    candidate = load_candidate()
    targets = [resolve_target(candidate, target) for target in load_targets()]
    arm = QueryArmV2(
        id="arm",
        target_id=targets[0].target.id,
        adapter="adzuna",
        query="AI implementation",
        geography="Remote",
        version="v1",
    )
    request = build_acquisition_request(targets, [arm], force_refresh=False)
    assert request["source"] == "all"
    assert request["score"] is False
    assert request["use_llm"] is False
    assert request["include_remote_sources"] is True
    assert request["locations"] == []
    assert request["experience_levels"] == []
    assert request["aggregator_query_arms"] == [
        {"query": "AI implementation", "geography": "Remote"}
    ]
    assert len(request["keywords"]) > 10


@pytest.mark.asyncio
async def test_shadow_run_persists_evaluations_but_creates_no_review_or_materials() -> None:
    tenant = f"test-v2-shadow-{uuid.uuid4().hex[:8]}"
    source_id = f"shadow-{uuid.uuid4().hex}"
    job = _job(source_id)
    captured: dict = {}

    async def fake_search(**kwargs):
        captured.update(kwargs)
        from src.application.jobs import serialize_job

        return {
            "jobs": [serialize_job(job)],
            "counts": {"raw_total": 1, "filtered_total": 1, "total": 1},
            "errors": [],
        }

    factory = get_session_factory()
    try:
        with (
            patch(
                "src.orchestration.portfolio_run.extract_job_facts",
                wraps=extract_job_facts,
            ) as facts_spy,
            patch(
                "src.orchestration.portfolio_run.assess_employer",
                wraps=assess_employer,
            ) as employer_spy,
            patch(
                "src.orchestration.portfolio_run.assess_posting",
                wraps=assess_posting,
            ) as posting_spy,
        ):
            result = await run_portfolio_v2(
                tenant_id=tenant,
                mode="v2_shadow",
                search_fn=fake_search,
                enqueue_fn=lambda *_: (_ for _ in ()).throw(AssertionError("shadow enqueue")),
                session_factory=factory,
            )
        assert facts_spy.call_count == 1
        assert employer_spy.call_count == 1
        assert posting_spy.call_count == 1
        assert result["ok"]
        assert result["pipeline_version"] == "v2_shadow"
        assert result["review_entry_ids"] == []
        assert result["materials_task_ids"] == []
        assert captured["source"] == "all"
        assert captured["use_llm"] is False

        replay = await run_portfolio_v2(
            tenant_id=tenant,
            mode="v2_shadow",
            search_fn=fake_search,
            enqueue_fn=lambda *_: (_ for _ in ()).throw(AssertionError("shadow enqueue")),
            session_factory=factory,
        )
        assert replay["ok"]
        assert len(replay["selected"]) == 1

        with factory() as session:
            runs = session.scalars(
                select(DiscoveryRun).where(DiscoveryRun.tenant_id == tenant)
            ).all()
            evaluations = session.scalars(
                select(JobTargetEvaluation).where(
                    JobTargetEvaluation.tenant_id == tenant
                )
            ).all()
            links = session.scalars(
                select(DiscoveryRunEvaluation).where(
                    DiscoveryRunEvaluation.tenant_id == tenant
                )
            ).all()
            decisions = session.scalars(
                select(PortfolioDecision).where(PortfolioDecision.tenant_id == tenant)
            ).all()
            reviews = session.scalars(
                select(ReviewQueueEntry).where(ReviewQueueEntry.tenant_id == tenant)
            ).all()
            assert len(runs) == 2
            assert len(evaluations) == 5
            assert len(links) == 10
            assert decisions
            assert reviews == []
    finally:
        with factory() as session, session.begin():
            evaluation_ids = list(
                session.scalars(
                    select(JobTargetEvaluation.id).where(
                        JobTargetEvaluation.tenant_id == tenant
                    )
                )
            )
            portfolio_ids = list(
                session.scalars(
                    select(PortfolioRun.id).where(PortfolioRun.tenant_id == tenant)
                )
            )
            session.execute(
                delete(PortfolioDecision).where(PortfolioDecision.tenant_id == tenant)
            )
            session.execute(delete(PortfolioRun).where(PortfolioRun.tenant_id == tenant))
            if evaluation_ids:
                session.execute(
                    delete(JobEvaluationReason).where(
                        JobEvaluationReason.evaluation_id.in_(evaluation_ids)
                    )
                )
            session.execute(
                delete(JobTargetEvaluation).where(JobTargetEvaluation.tenant_id == tenant)
            )
            session.execute(
                delete(SourceQueryRun).where(SourceQueryRun.tenant_id == tenant)
            )
            session.execute(
                delete(SourceQueryArm).where(SourceQueryArm.tenant_id == tenant)
            )
            session.execute(delete(DiscoveryRun).where(DiscoveryRun.tenant_id == tenant))
            posting_ids = list(
                session.scalars(
                    select(JobPosting.id).where(JobPosting.tenant_id == tenant)
                )
            )
            if posting_ids:
                session.execute(
                    delete(JobSnapshot).where(JobSnapshot.posting_id.in_(posting_ids))
                )
            session.execute(delete(JobPosting).where(JobPosting.tenant_id == tenant))
