"""Pure durable endpoint-health state machine for Job Pool V2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

EndpointState = Literal[
    "candidate", "active", "degraded", "quarantined", "dormant", "blocked", "retired"
]
FetchStatus = Literal[
    "nonempty",
    "empty",
    "not_found",
    "forbidden",
    "rate_limited",
    "timeout",
    "network_error",
    "malformed",
    "schema_drift",
    "compliance_blocked",
]


class EndpointHealthV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: EndpointState = "candidate"
    consecutive_failures: int = 0
    consecutive_empty: int = 0
    recovery_successes: int = 0
    first_failure_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    last_nonempty_at: datetime | None = None
    next_probe_at: datetime | None = None


def transition_health(
    current: EndpointHealthV2,
    status: FetchStatus,
    *,
    now: datetime | None = None,
    retry_after_seconds: int | None = None,
) -> EndpointHealthV2:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    values = current.model_dump()
    values["last_checked_at"] = now
    state = current.state

    if state == "retired":
        return current.model_copy(update={"last_checked_at": now})
    if status == "compliance_blocked" or status == "forbidden":
        values.update(state="blocked", next_probe_at=None, recovery_successes=0)
        return EndpointHealthV2(**values)
    if status == "rate_limited":
        values["next_probe_at"] = now + timedelta(seconds=max(60, retry_after_seconds or 3600))
        return EndpointHealthV2(**values)

    if status in {"nonempty", "empty"}:
        values.update(
            consecutive_failures=0,
            first_failure_at=None,
            last_success_at=now,
        )
        if status == "nonempty":
            values.update(consecutive_empty=0, last_nonempty_at=now)
        else:
            values["consecutive_empty"] = current.consecutive_empty + 1

        if state == "candidate" and status == "nonempty":
            state = "active"
            values["recovery_successes"] = 0
        elif state == "quarantined":
            state = "degraded"
            values["recovery_successes"] = 1
        elif state == "degraded":
            recovery = current.recovery_successes + 1
            values["recovery_successes"] = recovery
            if recovery >= 2:
                state = "active"
                values["recovery_successes"] = 0

        last_nonempty = values.get("last_nonempty_at")
        empty_too_long = (
            isinstance(last_nonempty, datetime) and now - last_nonempty >= timedelta(days=14)
        )
        if values["consecutive_empty"] >= 7 or empty_too_long:
            state = "dormant"
            values["next_probe_at"] = now + timedelta(days=7)
        else:
            values["next_probe_at"] = None
        values["state"] = state
        return EndpointHealthV2(**values)

    failures = current.consecutive_failures + 1
    first_failure = current.first_failure_at or now
    values.update(
        consecutive_failures=failures,
        first_failure_at=first_failure,
        recovery_successes=0,
    )
    hard_failure = status in {"not_found", "malformed", "schema_drift"}
    if hard_failure:
        if state in {"candidate", "active"}:
            state = "degraded"
        if failures >= 3 and now - first_failure >= timedelta(hours=24):
            state = "quarantined"
            values["next_probe_at"] = now + timedelta(days=7)
    elif failures >= 2 and state == "active":
        state = "degraded"
    values["state"] = state
    return EndpointHealthV2(**values)


def endpoint_is_due(health: EndpointHealthV2, *, now: datetime | None = None) -> bool:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    if health.state in {"blocked", "retired"}:
        return False
    return health.next_probe_at is None or health.next_probe_at <= now


__all__ = ["EndpointHealthV2", "endpoint_is_due", "transition_health"]
