"""SUP-01: per-source/endpoint supply funnel report.

DB-backed, following the pattern in ``test_portfolio_run_v2.py`` -- build
real rows under a throwaway tenant, call the use case, assert, clean up.
Needs Postgres+Redis up (see AGENTS.md); it will hang rather than fail if
Postgres is down.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from src.application.source_funnel import source_funnel_report
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
)

TARGET_ID = "ai-implementation"


def _evaluation_kwargs(**overrides):
    base = dict(
        candidate_version="cand-v1",
        target_version="target-v1",
        parser_version="parser-v1",
        role_taxonomy_version="role-v1",
        capability_taxonomy_version="cap-v1",
        scorer_version="job-pool-v2.4",
        pipeline_version="v2_shadow",
        stage_status="evaluated",
        facts={"full_description_available": True},
        gate_results=[],
        component_scores={"role": 70, "level": 60, "evidence": 60, "domain": 60},
        component_confidence={"role": 0.8},
        story_fit=0.7,
        candidacy_index=0.7,
        review_index=70.0,
        adjusted_review_index=70.0,
        confidence=0.7,
        explanation={"strengths": [], "gaps": []},
        employer_assessment={"confidence": 0.8},
    )
    base.update(overrides)
    return base


@pytest.fixture
def tenant():
    return f"test-source-funnel-{uuid.uuid4().hex[:8]}"


def _cleanup(factory, tenant_id: str) -> None:
    with factory() as session, session.begin():
        evaluation_ids = list(
            session.scalars(
                select(JobTargetEvaluation.id).where(
                    JobTargetEvaluation.tenant_id == tenant_id
                )
            )
        )
        portfolio_ids = list(
            session.scalars(select(PortfolioRun.id).where(PortfolioRun.tenant_id == tenant_id))
        )
        if portfolio_ids:
            session.execute(
                delete(PortfolioDecision).where(
                    PortfolioDecision.portfolio_run_id.in_(portfolio_ids)
                )
            )
            session.execute(delete(PortfolioRun).where(PortfolioRun.tenant_id == tenant_id))
        if evaluation_ids:
            session.execute(
                delete(JobEvaluationReason).where(
                    JobEvaluationReason.evaluation_id.in_(evaluation_ids)
                )
            )
            session.execute(
                delete(DiscoveryRunEvaluation).where(
                    DiscoveryRunEvaluation.evaluation_id.in_(evaluation_ids)
                )
            )
        session.execute(
            delete(JobTargetEvaluation).where(JobTargetEvaluation.tenant_id == tenant_id)
        )
        session.execute(delete(DiscoveryRun).where(DiscoveryRun.tenant_id == tenant_id))
        posting_ids = list(
            session.scalars(select(JobPosting.id).where(JobPosting.tenant_id == tenant_id))
        )
        if posting_ids:
            session.execute(delete(JobSnapshot).where(JobSnapshot.posting_id.in_(posting_ids)))
        session.execute(delete(JobPosting).where(JobPosting.tenant_id == tenant_id))


def test_funnel_reconciles_to_run_and_flags_low_yield_endpoint(tenant):
    """Builds one healthy employer-board endpoint and one low-yield aggregator
    query arm inside a single discovery run, then checks:

    - counts reconcile to the run and every stage traces back to real
      snapshot ids (SUP-01 acceptance #1);
    - the aggregator arm (many jobs, zero routed, zero full-JD) is visibly
      flagged low_yield (SUP-01 acceptance #2), while the healthy endpoint is
      not.
    """

    factory = get_session_factory()
    run_id = uuid.uuid4()
    now = datetime.now(UTC)

    all_snapshot_ids: set[str] = set()
    acme_posting_ids: list[uuid.UUID] = []
    aggregator_posting_ids: list[uuid.UUID] = []
    surfaced_evaluation_id: uuid.UUID | None = None

    try:
        with factory() as session, session.begin():
            session.add(
                DiscoveryRun(
                    id=run_id,
                    tenant_id=tenant,
                    mode="v2_shadow",
                    pipeline_version="v2_shadow",
                    config_hash="hash",
                    target_ids=[TARGET_ID],
                    status="complete",
                    started_at=now,
                    finished_at=now,
                )
            )

            # --- Healthy employer-board endpoint: greenhouse / Acme Corp ---
            # 3 fetched, 1 fails the location gate, all 3 route + reach A/B,
            # 1 gets surfaced.
            for i in range(3):
                posting = JobPosting(
                    tenant_id=tenant,
                    source="greenhouse",
                    source_id=f"acme-{i}",
                    company="Acme Corp",
                    state="new",
                )
                session.add(posting)
                session.flush()
                acme_posting_ids.append(posting.id)
                snapshot = JobSnapshot(
                    tenant_id=tenant,
                    posting_id=posting.id,
                    content_hash=f"acme-hash-{i}",
                    title="Implementation Specialist",
                    # SUP-01B: exact fetch-time tag (set by
                    # src.intake.search._fetch_board), not a company-name
                    # guess -- this is what real snapshots carry now.
                    raw_data={
                        "source_endpoint_adapter": "greenhouse",
                        "source_endpoint_key": "acme-corp",
                    },
                    scraped_at=now,
                )
                session.add(snapshot)
                session.flush()
                all_snapshot_ids.add(str(snapshot.id))

                location_pass = i != 0  # first one fails geography
                tier = "A" if i < 2 else "C"
                evaluation = JobTargetEvaluation(
                    tenant_id=tenant,
                    discovery_run_id=run_id,
                    snapshot_id=snapshot.id,
                    target_id=TARGET_ID,
                    tier=tier,
                    posting_assessment={"description_completeness": "full"},
                    **_evaluation_kwargs(),
                )
                session.add(evaluation)
                session.flush()
                session.add(
                    DiscoveryRunEvaluation(
                        tenant_id=tenant, discovery_run_id=run_id, evaluation_id=evaluation.id
                    )
                )
                session.add(
                    JobEvaluationReason(
                        tenant_id=tenant,
                        evaluation_id=evaluation.id,
                        discovery_run_id=run_id,
                        target_id=TARGET_ID,
                        stage="global_eligibility",
                        decision="pass" if location_pass else "fail",
                        reason_code="location_ok" if location_pass else "location_out_of_policy",
                        severity="info" if location_pass else "blocking",
                        details={"gate_id": "location"},
                    )
                )
                session.add(
                    JobEvaluationReason(
                        tenant_id=tenant,
                        evaluation_id=evaluation.id,
                        discovery_run_id=run_id,
                        target_id=TARGET_ID,
                        stage="target_routing",
                        decision="pass",
                        reason_code="route_core",
                        severity="info",
                        details={},
                    )
                )
                if i == 1:
                    surfaced_evaluation_id = evaluation.id

            # --- Low-yield aggregator arm: adzuna, "AI implementation"/"Remote" ---
            # 6 fetched, none route, none have a full JD -- classic
            # many-jobs-zero-yield endpoint.
            for i in range(6):
                posting = JobPosting(
                    tenant_id=tenant,
                    source="adzuna",
                    source_id=f"adzuna-{i}",
                    company=f"Aggregator Employer {i}",
                    state="new",
                )
                session.add(posting)
                session.flush()
                aggregator_posting_ids.append(posting.id)
                snapshot = JobSnapshot(
                    tenant_id=tenant,
                    posting_id=posting.id,
                    content_hash=f"adzuna-hash-{i}",
                    title="Something Adjacent",
                    raw_data={
                        "source_query_term": "AI implementation",
                        "source_query_location": "Remote",
                    },
                    scraped_at=now,
                )
                session.add(snapshot)
                session.flush()
                all_snapshot_ids.add(str(snapshot.id))

                evaluation = JobTargetEvaluation(
                    tenant_id=tenant,
                    discovery_run_id=run_id,
                    snapshot_id=snapshot.id,
                    target_id=TARGET_ID,
                    posting_assessment={"description_completeness": "snippet"},
                    **_evaluation_kwargs(tier="D"),
                )
                session.add(evaluation)
                session.flush()
                session.add(
                    DiscoveryRunEvaluation(
                        tenant_id=tenant, discovery_run_id=run_id, evaluation_id=evaluation.id
                    )
                )
                session.add(
                    JobEvaluationReason(
                        tenant_id=tenant,
                        evaluation_id=evaluation.id,
                        discovery_run_id=run_id,
                        target_id=TARGET_ID,
                        stage="global_eligibility",
                        decision="pass",
                        reason_code="location_ok",
                        severity="info",
                        details={"gate_id": "location"},
                    )
                )
                session.add(
                    JobEvaluationReason(
                        tenant_id=tenant,
                        evaluation_id=evaluation.id,
                        discovery_run_id=run_id,
                        target_id=TARGET_ID,
                        stage="target_routing",
                        decision="fail",
                        reason_code="route_unmatched",
                        severity="blocking",
                        details={},
                    )
                )

            portfolio_run = PortfolioRun(
                tenant_id=tenant,
                discovery_run_id=run_id,
                portfolio_id="default",
                portfolio_version="v1",
                config_hash="hash",
                mode="v2_shadow",
                seed="seed",
                status="complete",
                started_at=now,
                finished_at=now,
            )
            session.add(portfolio_run)
            session.flush()
            assert surfaced_evaluation_id is not None
            session.add(
                PortfolioDecision(
                    tenant_id=tenant,
                    portfolio_run_id=portfolio_run.id,
                    evaluation_id=surfaced_evaluation_id,
                    canonical_group="acme-1",
                    owned_target_id=TARGET_ID,
                    company_key="acme corp",
                    lane="core",
                    utility=1.0,
                    rank=1,
                    selected=True,
                )
            )

        with factory() as session:
            report = source_funnel_report(session, run_id=run_id, tenant_id=tenant)

        assert report["available"] is True
        assert report["run"]["id"] == str(run_id)

        by_key = {(row["source"], row["endpoint"]): row for row in report["sources"]}
        acme = by_key[("greenhouse", "acme-corp")]
        aggregator = by_key[("adzuna", "AI implementation / Remote")]

        # --- Reconciliation: every count traces to real snapshot ids ---
        assert set(acme["snapshot_ids"]) <= all_snapshot_ids
        assert set(aggregator["snapshot_ids"]) <= all_snapshot_ids
        assert len(acme["snapshot_ids"]) == 3
        assert len(aggregator["snapshot_ids"]) == 6

        # --- Healthy endpoint ---
        assert acme["funnel"]["fetched"] == 3
        assert acme["funnel"]["unique"] == 3
        assert acme["funnel"]["in_policy_geography"] == 2  # one posting failed geography
        assert acme["funnel"]["target_routed"] == 3
        assert acme["funnel"]["full_jd"] == 3
        assert acme["funnel"]["ab"] == 2  # tiers A, A, C -> 2 reach A/B
        assert acme["funnel"]["surfaced"] == 1
        assert acme["endpoint_kind"] == "employer_board"
        assert acme["low_yield"] is False
        assert acme["description_completeness"]["full"] == 3

        # --- Low-yield aggregator arm ---
        assert aggregator["funnel"]["fetched"] == 6
        assert aggregator["funnel"]["target_routed"] == 0
        assert aggregator["funnel"]["full_jd"] == 0
        assert aggregator["endpoint_kind"] == "aggregator_query_arm"
        assert aggregator["low_yield"] is True  # >= 5 fetched, zero routed/full-JD
        assert aggregator["description_completeness"]["snippet"] == 6

        # --- Totals reconcile across rows ---
        assert report["totals"]["fetched"] == acme["funnel"]["fetched"] + aggregator["funnel"]["fetched"]
        assert report["totals"]["surfaced"] == 1

        # --- Rolling yield windows are present (evaluations created "now") ---
        assert acme["yield"]["7d_unique_ab"] >= 2
        assert acme["yield"]["30d_unique_ab"] >= 2
        assert aggregator["yield"]["7d_unique_ab"] == 0

        # --- Fetch telemetry is honestly absent (no SourceEndpointRun/
        # SourceQueryRun rows were written for this synthetic run) ---
        assert acme["fetch"]["instrumented"] is False
        assert aggregator["fetch"]["instrumented"] is False
    finally:
        _cleanup(factory, tenant)


def test_untagged_ats_posting_is_attribution_unknown_not_guessed(tenant):
    """SUP-01B req #5: a direct-ATS posting with no fetch-time endpoint tag
    (e.g. it predates this instrumentation) must never be silently assigned
    to a guessed employer board. It should land in its own
    ``attribution_unknown`` bucket instead."""

    factory = get_session_factory()
    run_id = uuid.uuid4()
    now = datetime.now(UTC)
    try:
        with factory() as session, session.begin():
            session.add(
                DiscoveryRun(
                    id=run_id,
                    tenant_id=tenant,
                    mode="v2_shadow",
                    pipeline_version="v2_shadow",
                    config_hash="hash",
                    target_ids=[TARGET_ID],
                    status="complete",
                    started_at=now,
                    finished_at=now,
                )
            )
            posting = JobPosting(
                tenant_id=tenant,
                source="lever",
                source_id="untagged-1",
                company="Mystery Co",
                state="new",
            )
            session.add(posting)
            session.flush()
            snapshot = JobSnapshot(
                tenant_id=tenant,
                posting_id=posting.id,
                content_hash="untagged-hash",
                title="Something",
                raw_data={},  # no source_endpoint_key -- untagged/legacy
                scraped_at=now,
            )
            session.add(snapshot)
            session.flush()
            evaluation = JobTargetEvaluation(
                tenant_id=tenant,
                discovery_run_id=run_id,
                snapshot_id=snapshot.id,
                target_id=TARGET_ID,
                posting_assessment={"description_completeness": "partial"},
                **_evaluation_kwargs(tier="C"),
            )
            session.add(evaluation)
            session.flush()
            session.add(
                DiscoveryRunEvaluation(
                    tenant_id=tenant, discovery_run_id=run_id, evaluation_id=evaluation.id
                )
            )

        with factory() as session:
            report = source_funnel_report(session, run_id=run_id, tenant_id=tenant)

        by_key = {(row["source"], row["endpoint"]): row for row in report["sources"]}
        unknown = by_key[("lever", None)]
        assert unknown["endpoint_kind"] == "attribution_unknown"
        assert unknown["funnel"]["fetched"] == 1
        # It must NOT have been grouped under "Mystery Co" or any other
        # company-name-derived label -- that key shouldn't exist at all.
        assert ("lever", "Mystery Co") not in by_key
    finally:
        _cleanup(factory, tenant)


def test_multi_target_evaluations_are_not_double_counted(tenant):
    """SUP-01B req #7: one posting evaluated against three targets must still
    count once at fetched/unique -- not three times -- while stage flags
    (routed, A/B) reflect "at least one evaluation cleared it", proving the
    report rolls per-target pairs up to posting-level without inflation."""

    factory = get_session_factory()
    run_id = uuid.uuid4()
    now = datetime.now(UTC)
    try:
        with factory() as session, session.begin():
            session.add(
                DiscoveryRun(
                    id=run_id,
                    tenant_id=tenant,
                    mode="v2_shadow",
                    pipeline_version="v2_shadow",
                    config_hash="hash",
                    target_ids=["target-a", "target-b", "target-c"],
                    status="complete",
                    started_at=now,
                    finished_at=now,
                )
            )
            posting = JobPosting(
                tenant_id=tenant,
                source="greenhouse",
                source_id="multi-1",
                company="Multi Target Co",
                state="new",
            )
            session.add(posting)
            session.flush()
            snapshot = JobSnapshot(
                tenant_id=tenant,
                posting_id=posting.id,
                content_hash="multi-hash",
                title="Implementation Specialist",
                raw_data={
                    "source_endpoint_adapter": "greenhouse",
                    "source_endpoint_key": "multi-co",
                },
                scraped_at=now,
            )
            session.add(snapshot)
            session.flush()

            # Only the SECOND of three targets routes and reaches tier B --
            # the posting-level funnel should still show 1/1, not 3 or 0.
            for idx, (target_id, tier, routes) in enumerate(
                [("target-a", "D", False), ("target-b", "B", True), ("target-c", "D", False)]
            ):
                evaluation = JobTargetEvaluation(
                    tenant_id=tenant,
                    discovery_run_id=run_id,
                    snapshot_id=snapshot.id,
                    target_id=target_id,
                    posting_assessment={"description_completeness": "full"},
                    **_evaluation_kwargs(tier=tier),
                )
                session.add(evaluation)
                session.flush()
                session.add(
                    DiscoveryRunEvaluation(
                        tenant_id=tenant, discovery_run_id=run_id, evaluation_id=evaluation.id
                    )
                )
                session.add(
                    JobEvaluationReason(
                        tenant_id=tenant,
                        evaluation_id=evaluation.id,
                        discovery_run_id=run_id,
                        target_id=target_id,
                        stage="global_eligibility",
                        decision="pass",
                        reason_code="location_ok",
                        severity="info",
                        details={"gate_id": "location"},
                    )
                )
                session.add(
                    JobEvaluationReason(
                        tenant_id=tenant,
                        evaluation_id=evaluation.id,
                        discovery_run_id=run_id,
                        target_id=target_id,
                        stage="target_routing",
                        decision="pass" if routes else "fail",
                        reason_code="route_core" if routes else "route_unmatched",
                        severity="info" if routes else "blocking",
                        details={},
                    )
                )

        with factory() as session:
            report = source_funnel_report(session, run_id=run_id, tenant_id=tenant)

        by_key = {(row["source"], row["endpoint"]): row for row in report["sources"]}
        row = by_key[("greenhouse", "multi-co")]
        assert row["funnel"]["fetched"] == 1
        assert row["funnel"]["unique"] == 1
        assert row["funnel"]["in_policy_geography"] == 1
        assert row["funnel"]["target_routed"] == 1  # not 3 -- one posting, "at least one" pass
        assert row["funnel"]["ab"] == 1  # not 3 -- tier B on target-b only
        assert len(row["snapshot_ids"]) == 1  # one snapshot, referenced by all 3 evaluations
        assert report["totals"]["fetched"] == 1
    finally:
        _cleanup(factory, tenant)


def test_no_run_available_returns_not_available():
    factory = get_session_factory()
    with factory() as session:
        report = source_funnel_report(
            session, tenant_id=f"test-source-funnel-empty-{uuid.uuid4().hex[:8]}"
        )
    assert report["available"] is False


def test_run_with_no_linked_evaluations_reports_empty_sources(tenant):
    factory = get_session_factory()
    run_id = uuid.uuid4()
    now = datetime.now(UTC)
    try:
        with factory() as session, session.begin():
            session.add(
                DiscoveryRun(
                    id=run_id,
                    tenant_id=tenant,
                    mode="v2_shadow",
                    pipeline_version="v2_shadow",
                    config_hash="hash",
                    target_ids=[TARGET_ID],
                    status="complete",
                    started_at=now,
                    finished_at=now,
                )
            )
        with factory() as session:
            report = source_funnel_report(session, run_id=run_id, tenant_id=tenant)
        assert report["available"] is True
        assert report["sources"] == []
    finally:
        _cleanup(factory, tenant)
