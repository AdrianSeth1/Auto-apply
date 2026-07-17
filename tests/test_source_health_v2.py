from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.intake.source_health import EndpointHealthV2, endpoint_is_due, transition_health


NOW = datetime(2026, 7, 12, tzinfo=UTC)


def test_candidate_activates_only_on_schema_valid_nonempty_response() -> None:
    candidate = EndpointHealthV2()
    empty = transition_health(candidate, "empty", now=NOW)
    assert empty.state == "candidate"
    active = transition_health(candidate, "nonempty", now=NOW)
    assert active.state == "active"
    assert active.last_nonempty_at == NOW


def test_three_hard_failures_spanning_a_day_quarantine_without_deletion() -> None:
    health = EndpointHealthV2(state="active")
    health = transition_health(health, "not_found", now=NOW)
    assert health.state == "degraded"
    health = transition_health(health, "malformed", now=NOW + timedelta(hours=12))
    assert health.state == "degraded"
    health = transition_health(health, "schema_drift", now=NOW + timedelta(hours=25))
    assert health.state == "quarantined"
    assert not endpoint_is_due(health, now=NOW + timedelta(days=1))
    assert endpoint_is_due(health, now=NOW + timedelta(days=9))


def test_rate_limit_honors_retry_after_without_lowering_health() -> None:
    health = EndpointHealthV2(state="active")
    updated = transition_health(
        health, "rate_limited", now=NOW, retry_after_seconds=7200
    )
    assert updated.state == "active"
    assert updated.consecutive_failures == 0
    assert updated.next_probe_at == NOW + timedelta(hours=2)


def test_valid_empty_becomes_dormant_not_failed() -> None:
    health = EndpointHealthV2(state="active", last_nonempty_at=NOW)
    for day in range(1, 8):
        health = transition_health(health, "empty", now=NOW + timedelta(days=day))
    assert health.state == "dormant"
    assert health.consecutive_failures == 0
    assert health.consecutive_empty == 7


def test_quarantine_recovery_requires_two_successes() -> None:
    health = EndpointHealthV2(state="quarantined")
    health = transition_health(health, "nonempty", now=NOW)
    assert health.state == "degraded"
    assert health.recovery_successes == 1
    health = transition_health(health, "nonempty", now=NOW + timedelta(hours=1))
    assert health.state == "active"
    assert health.recovery_successes == 0


def test_compliance_block_is_immediate_and_not_due() -> None:
    blocked = transition_health(
        EndpointHealthV2(state="active"), "compliance_blocked", now=NOW
    )
    assert blocked.state == "blocked"
    assert not endpoint_is_due(blocked, now=NOW + timedelta(days=100))
