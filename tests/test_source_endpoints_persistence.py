"""SUP-01B: SourceEndpoint / SourceEndpointRun persistence.

DB-backed (see AGENTS.md -- needs Postgres up, or this hangs rather than
fails). Tests src.jobs.source_endpoints directly, independent of the full
run_portfolio_v2 orchestration, so endpoint-health bookkeeping is verified
in isolation from evaluation/scoring/portfolio logic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from src.core.database import get_session_factory
from src.core.models import DiscoveryRun, SourceEndpoint, SourceEndpointRun
from src.jobs.source_endpoints import record_endpoint_runs, upsert_source_endpoint


@pytest.fixture
def tenant():
    return f"test-source-endpoints-{uuid.uuid4().hex[:8]}"


def _cleanup(factory, tenant_id: str) -> None:
    with factory() as session, session.begin():
        endpoint_ids = list(
            session.scalars(select(SourceEndpoint.id).where(SourceEndpoint.tenant_id == tenant_id))
        )
        if endpoint_ids:
            session.execute(
                delete(SourceEndpointRun).where(SourceEndpointRun.endpoint_id.in_(endpoint_ids))
            )
        session.execute(delete(SourceEndpoint).where(SourceEndpoint.tenant_id == tenant_id))
        session.execute(delete(DiscoveryRun).where(DiscoveryRun.tenant_id == tenant_id))


def _discovery_run(session, *, tenant_id: str, started_at=None) -> DiscoveryRun:
    run = DiscoveryRun(
        tenant_id=tenant_id,
        mode="v2_shadow",
        pipeline_version="v2_shadow",
        config_hash="hash",
        target_ids=["ai-implementation"],
        status="complete",
        started_at=started_at or datetime.now(UTC),
        finished_at=started_at or datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    return run


def _metric(
    *,
    adapter: str = "greenhouse",
    endpoint_key: str = "acme",
    status: str = "success",
    provider_records: int = 5,
    normalized_records: int = 5,
    malformed_records: int = 0,
    error_code: str | None = None,
    error_detail: str | None = None,
    when: datetime | None = None,
) -> dict:
    when = when or datetime.now(UTC)
    return {
        "adapter": adapter,
        "endpoint_key": endpoint_key,
        "started_at": when,
        "finished_at": when + timedelta(milliseconds=250),
        "duration_ms": 250,
        "status": status,
        "http_status": None,
        "error_code": error_code,
        "error_detail": error_detail,
        "provider_records": provider_records,
        "normalized_records": normalized_records,
        "malformed_records": malformed_records,
        "from_cache": False,
    }


def test_one_row_per_attempted_endpoint_with_real_counts_no_fabrication(tenant):
    factory = get_session_factory()
    try:
        with factory() as session, session.begin():
            run = _discovery_run(session, tenant_id=tenant)
            metrics = [
                _metric(
                    adapter="greenhouse",
                    endpoint_key="acme",
                    provider_records=12,
                    normalized_records=10,
                    malformed_records=2,
                ),
                _metric(
                    adapter="lever",
                    endpoint_key="broken-co",
                    status="error",
                    provider_records=0,
                    normalized_records=0,
                    error_code="ScraperError",
                    error_detail="HTTP 500",
                ),
            ]
            run_ids = record_endpoint_runs(
                session, tenant_id=tenant, discovery_run_id=run.id, endpoint_metrics=metrics
            )
            assert len(run_ids) == 2
            assert ("greenhouse", "acme") in run_ids
            assert ("lever", "broken-co") in run_ids

        with factory() as session:
            rows = session.scalars(
                select(SourceEndpointRun).where(SourceEndpointRun.discovery_run_id == run.id)
            ).all()
            assert len(rows) == 2
            by_key = {}
            for row in rows:
                endpoint = session.get(SourceEndpoint, row.endpoint_id)
                by_key[(endpoint.adapter, endpoint.endpoint_key)] = row

            healthy = by_key[("greenhouse", "acme")]
            assert healthy.status == "success"
            assert healthy.provider_records == 12
            assert healthy.normalized_records == 10
            assert healthy.malformed_records == 2
            assert healthy.duration_ms == 250

            broken = by_key[("lever", "broken-co")]
            assert broken.status == "error"
            assert broken.error_code == "ScraperError"
            # Zero real records observed on failure -- not fabricated as
            # equal to some other number.
            assert broken.provider_records == 0
            assert broken.normalized_records == 0

            endpoints = session.scalars(
                select(SourceEndpoint).where(SourceEndpoint.tenant_id == tenant)
            ).all()
            assert len(endpoints) == 2
    finally:
        _cleanup(factory, tenant)


def test_upsert_reuses_existing_endpoint_and_never_touches_activation_state(tenant):
    factory = get_session_factory()
    try:
        with factory() as session, session.begin():
            first = upsert_source_endpoint(
                session, tenant_id=tenant, adapter="greenhouse", endpoint_key="acme"
            )
            first_id = first.id
            assert first.state == "candidate"  # model default -- never silently activated
            assert first.compliance_status == "unknown"
            # Simulate a human/SUP-02 workflow having since promoted it.
            first.state = "active"
            first.compliance_status = "verified"

        with factory() as session, session.begin():
            second = upsert_source_endpoint(
                session, tenant_id=tenant, adapter="greenhouse", endpoint_key="acme"
            )
            assert second.id == first_id
            # upsert must never revert an operator's activation decision.
            assert second.state == "active"
            assert second.compliance_status == "verified"

        with factory() as session:
            endpoints = session.scalars(
                select(SourceEndpoint).where(
                    SourceEndpoint.tenant_id == tenant,
                    SourceEndpoint.adapter == "greenhouse",
                    SourceEndpoint.endpoint_key == "acme",
                )
            ).all()
            assert len(endpoints) == 1  # no duplicate row created on the second call
    finally:
        _cleanup(factory, tenant)


def test_endpoint_health_tracks_failures_and_recovery_across_runs(tenant):
    """Fail, fail, then succeed -- consecutive_failures should climb then
    reset, and recovery_successes should tick up exactly once, on the
    recovering call.

    SUP-09 note: three warm-up successful runs precede the fail/fail/success
    sequence below. SUP-09 wired the real state machine
    (``src.intake.source_health.transition_health``) into endpoint-health
    updates and, per Phase S6 ("give new endpoints an exploration budget for
    three runs"), an endpoint's first three runs cannot trigger a *state*
    demotion (degraded/quarantined/etc.) even though the underlying counters
    (consecutive_failures, etc.) still update truthfully. Without the
    warm-up runs, this test's fail/fail/success sequence would itself fall
    entirely inside that budget window: the second failure would never
    actually reach "degraded" (state held at "active" by the budget), so
    there would be nothing to "recover" from and recovery_successes would
    correctly stay 0 instead of reaching 1 -- a real, intentional behavior
    difference from before SUP-09, not a bug. The warm-up runs make this
    test exercise the same failure/recovery sequence it always has, past
    the point where the exploration budget applies, so its original
    assertions still mean what they say.
    """

    factory = get_session_factory()
    try:
        with factory() as session, session.begin():
            warmup = _discovery_run(
                session, tenant_id=tenant, started_at=datetime.now(UTC) - timedelta(days=5)
            )
            record_endpoint_runs(
                session,
                tenant_id=tenant,
                discovery_run_id=warmup.id,
                endpoint_metrics=[
                    _metric(
                        status="success",
                        provider_records=3,
                        normalized_records=3,
                        when=datetime.now(UTC) - timedelta(days=5),
                    )
                ],
            )
        with factory() as session, session.begin():
            warmup2 = _discovery_run(
                session, tenant_id=tenant, started_at=datetime.now(UTC) - timedelta(days=4)
            )
            record_endpoint_runs(
                session,
                tenant_id=tenant,
                discovery_run_id=warmup2.id,
                endpoint_metrics=[
                    _metric(
                        status="success",
                        provider_records=3,
                        normalized_records=3,
                        when=datetime.now(UTC) - timedelta(days=4),
                    )
                ],
            )
        with factory() as session, session.begin():
            warmup3 = _discovery_run(
                session, tenant_id=tenant, started_at=datetime.now(UTC) - timedelta(days=3)
            )
            record_endpoint_runs(
                session,
                tenant_id=tenant,
                discovery_run_id=warmup3.id,
                endpoint_metrics=[
                    _metric(
                        status="success",
                        provider_records=3,
                        normalized_records=3,
                        when=datetime.now(UTC) - timedelta(days=3),
                    )
                ],
            )

        with factory() as session, session.begin():
            run1 = _discovery_run(
                session, tenant_id=tenant, started_at=datetime.now(UTC) - timedelta(days=2)
            )
            record_endpoint_runs(
                session,
                tenant_id=tenant,
                discovery_run_id=run1.id,
                endpoint_metrics=[
                    _metric(
                        status="error",
                        provider_records=0,
                        normalized_records=0,
                        error_code="ScraperError",
                    )
                ],
            )

        with factory() as session, session.begin():
            run2 = _discovery_run(
                session, tenant_id=tenant, started_at=datetime.now(UTC) - timedelta(days=1)
            )
            record_endpoint_runs(
                session,
                tenant_id=tenant,
                discovery_run_id=run2.id,
                endpoint_metrics=[
                    _metric(
                        status="error",
                        provider_records=0,
                        normalized_records=0,
                        error_code="ScraperError",
                    )
                ],
            )

        with factory() as session:
            endpoint = session.scalar(
                select(SourceEndpoint).where(
                    SourceEndpoint.tenant_id == tenant,
                    SourceEndpoint.adapter == "greenhouse",
                    SourceEndpoint.endpoint_key == "acme",
                )
            )
            assert endpoint.consecutive_failures == 2
            assert endpoint.first_failure_at is not None
            assert endpoint.recovery_successes == 0
            assert (
                endpoint.state == "degraded"
            )  # SUP-09: now a real transition, past the budget window

        with factory() as session, session.begin():
            run3 = _discovery_run(session, tenant_id=tenant)
            record_endpoint_runs(
                session,
                tenant_id=tenant,
                discovery_run_id=run3.id,
                endpoint_metrics=[
                    _metric(status="success", provider_records=4, normalized_records=4)
                ],
            )

        with factory() as session:
            endpoint = session.scalar(
                select(SourceEndpoint).where(
                    SourceEndpoint.tenant_id == tenant,
                    SourceEndpoint.adapter == "greenhouse",
                    SourceEndpoint.endpoint_key == "acme",
                )
            )
            assert endpoint.consecutive_failures == 0
            assert endpoint.recovery_successes == 1
            assert endpoint.last_success_at is not None
            assert endpoint.last_nonempty_at is not None
            assert endpoint.consecutive_empty == 0
            assert (
                endpoint.state == "degraded"
            )  # SUP-09: 1 recovery is not yet 2 -- transition_health's own rule
    finally:
        _cleanup(factory, tenant)


def test_new_endpoint_exploration_budget_suppresses_early_demotion(tenant):
    """SUP-09 / Phase S6: an endpoint's first three runs cannot demote its
    state even on real failures -- counters still update truthfully. An
    endpoint that has never succeeded remains a candidate; the budget
    does not fabricate an activation signal."""

    factory = get_session_factory()
    try:
        with factory() as session, session.begin():
            run1 = _discovery_run(
                session, tenant_id=tenant, started_at=datetime.now(UTC) - timedelta(hours=2)
            )
            record_endpoint_runs(
                session,
                tenant_id=tenant,
                discovery_run_id=run1.id,
                endpoint_metrics=[
                    _metric(
                        status="error",
                        provider_records=0,
                        normalized_records=0,
                        error_code="ScraperError",
                    )
                ],
            )
        with factory() as session, session.begin():
            run2 = _discovery_run(
                session, tenant_id=tenant, started_at=datetime.now(UTC) - timedelta(hours=1)
            )
            record_endpoint_runs(
                session,
                tenant_id=tenant,
                discovery_run_id=run2.id,
                endpoint_metrics=[
                    _metric(
                        status="error",
                        provider_records=0,
                        normalized_records=0,
                        error_code="ScraperError",
                    )
                ],
            )

        with factory() as session:
            endpoint = session.scalar(
                select(SourceEndpoint).where(
                    SourceEndpoint.tenant_id == tenant,
                    SourceEndpoint.adapter == "greenhouse",
                    SourceEndpoint.endpoint_key == "acme",
                )
            )
            # This endpoint has never succeeded, so it remains a candidate.
            # The exploration budget prevents an early demotion but does not
            # fabricate the successful fetch needed for candidate -> active.
            assert endpoint.consecutive_failures == 2
            assert endpoint.state == "candidate"
    finally:
        _cleanup(factory, tenant)


def test_empty_success_counts_as_reached_but_not_nonempty(tenant):
    """A board that responds successfully with zero postings is a real,
    healthy contact (last_success_at moves) but not a nonempty one
    (last_nonempty_at doesn't) -- distinct signals, neither fabricated."""

    factory = get_session_factory()
    try:
        with factory() as session, session.begin():
            run = _discovery_run(session, tenant_id=tenant)
            record_endpoint_runs(
                session,
                tenant_id=tenant,
                discovery_run_id=run.id,
                endpoint_metrics=[
                    _metric(status="empty", provider_records=0, normalized_records=0)
                ],
            )

        with factory() as session:
            endpoint = session.scalar(
                select(SourceEndpoint).where(
                    SourceEndpoint.tenant_id == tenant,
                    SourceEndpoint.adapter == "greenhouse",
                    SourceEndpoint.endpoint_key == "acme",
                )
            )
            assert endpoint.last_success_at is not None
            assert endpoint.last_nonempty_at is None
            assert endpoint.consecutive_empty == 1
            assert endpoint.consecutive_failures == 0
    finally:
        _cleanup(factory, tenant)
