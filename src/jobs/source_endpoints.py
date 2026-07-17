"""SUP-01B / SUP-09: real per-endpoint fetch bookkeeping for the Job Pool V2
ledger, and its health-state transitions.

Turns the ``endpoint_metrics`` list collected by
``src.intake.search._fetch_board`` into persisted ``SourceEndpoint`` /
``SourceEndpointRun`` rows. Kept out of ``src.intake.search`` on purpose --
that module has no DB dependency today and this keeps it that way; the
caller (``src.orchestration.portfolio_run``) already owns the transaction
these rows belong in.

This module does not touch ``SourceEndpoint.compliance_status`` and never
flips a row into "enabled"/verified. Creating the passive bookkeeping row
here is not the same as verifying and activating a source -- that remains
SUP-02's job (``scripts/probe_employer_cohort.py``), which must still write
an explicit, reviewed YAML patch before anything is trusted. New rows are
created with the model's own defaults (``state="candidate"``,
``compliance_status="unknown"``).

SUP-09 (Phase S6, "quarantine repeated failures using existing source-health
rules"): ``_update_endpoint_health`` now actually drives ``SourceEndpoint.state``
through the real state machine in ``src.intake.source_health.transition_health``
instead of only accumulating counters that nothing consumed -- that gap was
flagged as a known follow-up in SUP-01B/SUP-02 and is closed here. Two things
worth knowing about how this is wired:

- ``_fetch_board`` only reports four coarse statuses (``success``, ``empty``,
  ``error``, ``cache_hit``); ``transition_health`` wants the finer
  ``FetchStatus`` vocabulary (``not_found``, ``forbidden``, ``rate_limited``,
  etc.). ``_classify_fetch_status`` bridges the two by parsing the HTTP
  status code every scraper's own ``ScraperError`` already embeds in its
  message (``src/intake/base.py::BaseScraper._get``: ``f"HTTP {status} from
  {url}"`` -- confirmed against the real scraper code, not guessed) rather
  than widening the scraper interface. An error with no parseable HTTP code
  (connection/timeout failures) falls to ``timeout`` or ``network_error``
  based on the exception class name.
- **Exploration budget** (Phase S6: "Give new endpoints an exploration
  budget for three runs"): an endpoint's first three ``SourceEndpointRun``
  rows can still update the passive counters truthfully, but cannot trigger
  a *state* demotion (degraded/quarantined/dormant) -- a brand-new endpoint
  having one or two rough runs shouldn't get quarantined before it's had a
  fair shot. This budget never protects a ``blocked`` transition
  (compliance/403 signals apply immediately, exploration budget or not --
  those aren't noise, they're the endpoint telling us to stop). Computed
  from a real ``COUNT(*)`` of this endpoint's prior runs, not a new column,
  so no migration was needed for this.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.models import TENANT_DEFAULT, SourceEndpoint, SourceEndpointRun
from src.intake.source_health import EndpointHealthV2, transition_health

_EXPLORATION_BUDGET_RUNS = 3
# Only unhealthy/dormant state changes are held during exploration. A
# successful first fetch must still promote candidate -> active; suppressing
# that promotion leaves every newly observed endpoint stuck as a candidate.
_EXPLORATION_DEMOTION_STATES = frozenset({"degraded", "quarantined", "dormant"})
# State transitions that must never be suppressed by the exploration
# budget -- these represent the endpoint (or its host) actively telling us
# to stop, not ordinary noise a new endpoint should get a fair shake against.
_ALWAYS_APPLY_STATES = frozenset({"blocked"})

_HTTP_STATUS_RE = re.compile(r"HTTP (\d{3})")


def upsert_source_endpoint(
    session: Session,
    *,
    tenant_id: str,
    adapter: str,
    endpoint_key: str,
) -> SourceEndpoint:
    """Find-or-create the passive bookkeeping row for one configured board.

    Never changes ``state``/``compliance_status`` on an existing row.
    """
    existing = session.scalar(
        select(SourceEndpoint).where(
            SourceEndpoint.tenant_id == tenant_id,
            SourceEndpoint.adapter == adapter,
            SourceEndpoint.endpoint_key == endpoint_key,
        )
    )
    if existing is not None:
        return existing
    endpoint = SourceEndpoint(
        tenant_id=tenant_id,
        adapter=adapter,
        endpoint_key=endpoint_key,
    )
    session.add(endpoint)
    session.flush()
    return endpoint


def record_endpoint_runs(
    session: Session,
    *,
    tenant_id: str = TENANT_DEFAULT,
    discovery_run_id: uuid.UUID,
    endpoint_metrics: list[dict],
) -> dict[tuple[str, str], uuid.UUID]:
    """Write one ``SourceEndpointRun`` per attempted endpoint for this run.

    ``endpoint_metrics`` is the out-parameter list populated by
    ``src.intake.search.search_jobs(endpoint_metrics=...)`` -- one dict per
    attempted board (success, empty, error, or cache hit), never fabricated
    here: whatever counts/timestamps the fetch boundary actually observed.

    Returns a ``{(adapter, endpoint_key): source_endpoint_run_id}`` lookup
    so the caller can attribute JobSnapshots created in the same run back to
    the exact fetch that produced them.
    """
    run_ids: dict[tuple[str, str], uuid.UUID] = {}
    for metric in endpoint_metrics:
        adapter = metric["adapter"]
        endpoint_key = metric["endpoint_key"]
        endpoint = upsert_source_endpoint(
            session, tenant_id=tenant_id, adapter=adapter, endpoint_key=endpoint_key
        )
        _update_endpoint_health(session, endpoint, metric)

        fetch_run_id = f"{discovery_run_id}:{adapter}:{endpoint_key}"
        row = SourceEndpointRun(
            tenant_id=tenant_id,
            endpoint_id=endpoint.id,
            discovery_run_id=discovery_run_id,
            fetch_run_id=fetch_run_id,
            status=metric["status"],
            http_status=metric.get("http_status"),
            provider_records=metric.get("provider_records") or 0,
            normalized_records=metric.get("normalized_records") or 0,
            malformed_records=metric.get("malformed_records") or 0,
            duration_ms=metric.get("duration_ms"),
            error_code=metric.get("error_code"),
            error_detail=metric.get("error_detail"),
            started_at=metric["started_at"],
            finished_at=metric["finished_at"],
        )
        session.add(row)
        session.flush()
        run_ids[(adapter, endpoint_key)] = row.id
    return run_ids


def _classify_fetch_status(metric: dict) -> str:
    """Map ``_fetch_board``'s four coarse statuses to a real ``FetchStatus``.

    Grounded in the actual error-message shape every scraper's
    ``BaseScraper._get`` produces (``f"HTTP {status} from {url}"``) rather
    than guessed -- see the module docstring. Falls back to the safer,
    more generic ``network_error`` bucket whenever nothing more specific
    can be determined; it never guesses a hard-failure classification
    (``not_found``/``malformed``/``schema_drift``) it can't actually see
    evidence for, since those trigger faster quarantine in
    ``transition_health``.
    """
    coarse = metric.get("status")
    if coarse in ("success", "cache_hit"):
        return "nonempty" if (metric.get("normalized_records") or 0) > 0 else "empty"
    if coarse == "empty":
        return "empty"
    if coarse != "error":
        return "network_error"

    detail = str(metric.get("error_detail") or "")
    code = str(metric.get("error_code") or "")
    match = _HTTP_STATUS_RE.search(detail)
    if match:
        http_status = int(match.group(1))
        if http_status == 404:
            return "not_found"
        if http_status in (401, 403):
            return "forbidden"
        if http_status == 429:
            return "rate_limited"
        # Other 4xx/5xx: a real HTTP response came back, just not one of
        # the specifically-handled codes -- network_error keeps this in
        # the "soft failure, can recover" bucket rather than escalating to
        # a hard failure this classifier has no real evidence for.
        return "network_error"
    if "timeout" in code.lower() or "timeout" in detail.lower():
        return "timeout"
    return "network_error"


def _update_endpoint_health(session: Session, endpoint: SourceEndpoint, metric: dict) -> None:
    now = metric["finished_at"]
    status = _classify_fetch_status(metric)

    current = EndpointHealthV2(
        state=endpoint.state,  # type: ignore[arg-type]
        consecutive_failures=endpoint.consecutive_failures,
        consecutive_empty=endpoint.consecutive_empty,
        recovery_successes=endpoint.recovery_successes,
        first_failure_at=endpoint.first_failure_at,
        last_checked_at=endpoint.last_checked_at,
        last_success_at=endpoint.last_success_at,
        last_nonempty_at=endpoint.last_nonempty_at,
        next_probe_at=endpoint.next_probe_at,
    )
    updated = transition_health(current, status, now=now)  # type: ignore[arg-type]

    if updated.state in _EXPLORATION_DEMOTION_STATES and updated.state != current.state:
        prior_runs = (
            session.scalar(
                select(func.count())
                .select_from(SourceEndpointRun)
                .where(SourceEndpointRun.endpoint_id == endpoint.id)
            )
            or 0
        )
        if prior_runs < _EXPLORATION_BUDGET_RUNS:
            # Exploration budget: record the real counters (truthful --
            # doesn't hide that this run failed/was empty) but don't let a
            # brand-new endpoint's state be demoted before it's had three
            # full runs. next_probe_at is cleared too, since suppressing
            # the state change while keeping a quarantine-style probe
            # delay would be an inconsistent half-measure.
            updated = updated.model_copy(update={"state": current.state, "next_probe_at": None})

    endpoint.state = updated.state
    endpoint.consecutive_failures = updated.consecutive_failures
    endpoint.consecutive_empty = updated.consecutive_empty
    endpoint.recovery_successes = updated.recovery_successes
    endpoint.first_failure_at = updated.first_failure_at
    endpoint.last_checked_at = updated.last_checked_at
    endpoint.last_success_at = updated.last_success_at
    endpoint.last_nonempty_at = updated.last_nonempty_at
    endpoint.next_probe_at = updated.next_probe_at


__all__ = ["upsert_source_endpoint", "record_endpoint_runs"]
