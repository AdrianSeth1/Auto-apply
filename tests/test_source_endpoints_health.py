"""Tests for the SUP-09 health-state wiring in src.jobs.source_endpoints.

``_classify_fetch_status`` is pure Python -- no DB needed. ``_update_endpoint_health``
needs a session only for one ``COUNT(*)`` query (the exploration-budget
check), so it's tested here against a minimal duck-typed fake session
rather than a real Postgres connection -- no other session method is ever
called by the code under test. ``compute_yield_demotion_candidates`` (in
``src.application.source_funnel``) does much heavier multi-table querying
and is DB-backed only; see the note in
the "SUP-09" notes in git history of the removed
``docs/JOB_POOL_V2_IMPLEMENTATION_STATUS.md`` (2026-07-16) for why that
one isn't exercised in this sandbox.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.core.models import SourceEndpoint
from src.jobs.source_endpoints import _classify_fetch_status, _update_endpoint_health

# ---- _classify_fetch_status -------------------------------------------------


@pytest.mark.parametrize(
    "metric,expected",
    [
        ({"status": "success", "normalized_records": 5}, "nonempty"),
        ({"status": "success", "normalized_records": 0}, "empty"),
        ({"status": "cache_hit", "normalized_records": 3}, "nonempty"),
        ({"status": "cache_hit", "normalized_records": 0}, "empty"),
        ({"status": "empty", "normalized_records": 0}, "empty"),
    ],
)
def test_classify_non_error_statuses(metric, expected):
    assert _classify_fetch_status(metric) == expected


def test_classify_404_as_not_found():
    metric = {
        "status": "error",
        "error_code": "ScraperError",
        "error_detail": "HTTP 404 from https://boards-api.greenhouse.io/v1/boards/x/jobs",
    }
    assert _classify_fetch_status(metric) == "not_found"


def test_classify_403_as_forbidden():
    metric = {
        "status": "error",
        "error_code": "ScraperError",
        "error_detail": "HTTP 403 from https://api.example.com/jobs",
    }
    assert _classify_fetch_status(metric) == "forbidden"


def test_classify_401_as_forbidden():
    metric = {
        "status": "error",
        "error_code": "ScraperError",
        "error_detail": "HTTP 401 from https://api.example.com/jobs",
    }
    assert _classify_fetch_status(metric) == "forbidden"


def test_classify_429_as_rate_limited():
    metric = {
        "status": "error",
        "error_code": "ScraperError",
        "error_detail": "HTTP 429 from https://api.example.com/jobs",
    }
    assert _classify_fetch_status(metric) == "rate_limited"


def test_classify_5xx_as_network_error_not_hard_failure():
    metric = {
        "status": "error",
        "error_code": "ScraperError",
        "error_detail": "HTTP 503 from https://api.example.com/jobs",
    }
    assert _classify_fetch_status(metric) == "network_error"


def test_classify_timeout_exception_class_name():
    metric = {"status": "error", "error_code": "ReadTimeout", "error_detail": "timed out"}
    assert _classify_fetch_status(metric) == "timeout"


def test_classify_connect_error_falls_to_network_error():
    metric = {"status": "error", "error_code": "ConnectError", "error_detail": "connection refused"}
    assert _classify_fetch_status(metric) == "network_error"


def test_classify_unrecognized_coarse_status_falls_to_network_error():
    assert _classify_fetch_status({"status": "something_new"}) == "network_error"


# ---- _update_endpoint_health -------------------------------------------------


class _FakeSession:
    """Duck-typed stand-in exposing only .scalar(), the one call
    _update_endpoint_health makes on a session."""

    def __init__(self, prior_run_count: int):
        self.prior_run_count = prior_run_count

    def scalar(self, _stmt):
        return self.prior_run_count


def _new_endpoint(**overrides) -> SourceEndpoint:
    # SQLAlchemy column-level `default=` only applies at INSERT/flush time,
    # not on plain Python construction -- these tests never touch a real
    # session/flush, so every field _update_endpoint_health reads must be
    # given an explicit value here (matching the model's intended defaults)
    # rather than relying on ORM defaults that would never fire.
    endpoint = SourceEndpoint(adapter="greenhouse", endpoint_key="acme")
    endpoint.state = "active"
    endpoint.consecutive_failures = 0
    endpoint.consecutive_empty = 0
    endpoint.recovery_successes = 0
    endpoint.first_failure_at = None
    endpoint.last_checked_at = None
    endpoint.last_success_at = None
    endpoint.last_nonempty_at = None
    endpoint.next_probe_at = None
    for key, value in overrides.items():
        setattr(endpoint, key, value)
    return endpoint


def _metric(*, status="success", normalized_records=1, **overrides):
    base = {
        "status": status,
        "normalized_records": normalized_records,
        "finished_at": datetime.now(UTC),
        "error_code": None,
        "error_detail": None,
    }
    base.update(overrides)
    return base


def test_successful_fetch_recovers_and_updates_timestamps():
    endpoint = _new_endpoint(state="active", consecutive_failures=0)
    session = _FakeSession(prior_run_count=10)
    metric = _metric(status="success", normalized_records=5)

    _update_endpoint_health(session, endpoint, metric)

    assert endpoint.state == "active"
    assert endpoint.last_nonempty_at == metric["finished_at"]
    assert endpoint.consecutive_empty == 0


def test_successful_first_fetch_promotes_candidate_inside_exploration_budget():
    endpoint = _new_endpoint(state="candidate")
    session = _FakeSession(prior_run_count=0)

    _update_endpoint_health(
        session,
        endpoint,
        _metric(status="success", normalized_records=5),
    )

    assert endpoint.state == "active"


def test_three_consecutive_hard_failures_quarantine_after_exploration_budget():
    endpoint = _new_endpoint(state="active", consecutive_failures=0, first_failure_at=None)
    session = _FakeSession(prior_run_count=10)  # well past the 3-run exploration budget
    now = datetime.now(UTC)

    # Two failures 24h apart to satisfy transition_health's min-span check.
    metric1 = _metric(
        status="error",
        error_detail="HTTP 404 from https://boards-api.greenhouse.io/v1/boards/x/jobs",
        finished_at=now,
    )
    _update_endpoint_health(session, endpoint, metric1)
    assert endpoint.state == "degraded"

    metric2 = _metric(
        status="error",
        error_detail="HTTP 404 from https://boards-api.greenhouse.io/v1/boards/x/jobs",
        finished_at=now + timedelta(hours=1),
    )
    _update_endpoint_health(session, endpoint, metric2)
    assert endpoint.state == "degraded"  # 2 failures, still under the 3-failure/24h bar

    metric3 = _metric(
        status="error",
        error_detail="HTTP 404 from https://boards-api.greenhouse.io/v1/boards/x/jobs",
        finished_at=now + timedelta(hours=25),
    )
    _update_endpoint_health(session, endpoint, metric3)
    assert endpoint.state == "quarantined"
    assert endpoint.next_probe_at is not None


def test_exploration_budget_suppresses_demotion_for_new_endpoint():
    endpoint = _new_endpoint(state="active", consecutive_failures=0, first_failure_at=None)
    now = datetime.now(UTC)

    # prior_run_count starts at 0 -- this endpoint is brand new.
    session = _FakeSession(prior_run_count=0)
    metric1 = _metric(
        status="error",
        error_detail="HTTP 404 from https://boards-api.greenhouse.io/v1/boards/x/jobs",
        finished_at=now,
    )
    _update_endpoint_health(session, endpoint, metric1)
    # transition_health alone would set "degraded" here, but budget applies
    # (prior_run_count=0 < 3), so state must not move.
    assert endpoint.state == "active"
    # The failure count itself is still recorded truthfully.
    assert endpoint.consecutive_failures == 1


def test_exploration_budget_never_suppresses_a_blocked_transition():
    endpoint = _new_endpoint(state="active")
    session = _FakeSession(prior_run_count=0)  # brand new endpoint
    metric = _metric(
        status="error",
        error_detail="HTTP 403 from https://api.example.com/jobs",
        finished_at=datetime.now(UTC),
    )

    _update_endpoint_health(session, endpoint, metric)

    # Forbidden/compliance signals apply immediately regardless of budget.
    assert endpoint.state == "blocked"


def test_exploration_budget_lifts_after_three_runs():
    endpoint = _new_endpoint(state="active", consecutive_failures=0, first_failure_at=None)
    now = datetime.now(UTC)

    # prior_run_count=3 means this is (at least) the 4th run -- budget lifted.
    session = _FakeSession(prior_run_count=3)
    metric = _metric(
        status="error",
        error_detail="HTTP 404 from https://boards-api.greenhouse.io/v1/boards/x/jobs",
        finished_at=now,
    )
    _update_endpoint_health(session, endpoint, metric)
    assert endpoint.state == "degraded"
