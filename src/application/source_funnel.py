"""SUP-01 / SUP-01B: per-source/endpoint supply funnel over the Job Pool V2
ledger.

Extends the nontechnical audit in ``job_pool_reports.py`` with one row per
source (and, where the data distinguishes it, per endpoint/query arm)
showing where jobs from that source are lost:

    fetched -> unique -> in-policy geography -> target-routed -> full-JD -> A/B -> surfaced

Design notes / honest limitations (read before "fixing" the zeros):

- SUP-01B wired real per-endpoint fetch telemetry: ``src.intake.search.
  _fetch_board`` times every attempted board (success/empty/error/cache-hit)
  and reports it via an ``endpoint_metrics`` out-parameter;
  ``src.jobs.source_endpoints.record_endpoint_runs`` (called from
  ``src.orchestration.portfolio_run``) turns that into real
  ``SourceEndpointRun`` rows, and real ``routed_pairs``/``viable_evaluations``
  land on ``SourceQueryRun`` after evaluation. A run only shows
  ``fetch.instrumented: True`` for a source/endpoint when a matching
  ``SourceEndpointRun``/``SourceQueryRun`` row actually exists for THAT run
  -- older runs predating this instrumentation, or any adapter that isn't
  wired through ``_fetch_board`` (LinkedIn is deliberately excluded from
  "all" entirely; see AGENTS.md), still fall back to ``instrumented: False``
  with ``fetch.duration_ms`` / ``fetch.fetched_provider_records`` left
  ``None`` rather than fabricated.
- Posting-to-endpoint attribution is exact, not guessed (SUP-01B req #5):
  Adzuna postings carry ``raw_data.source_query_term`` /
  ``source_query_location``; direct-ATS postings carry
  ``raw_data.source_endpoint_key`` (the literal companies.yaml slug, or
  ``tenant/host/site`` for Workday), both tagged once, at fetch time, by the
  code that actually knows which board produced the job -- never re-derived
  from company name after the fact. A multi-endpoint adapter posting with
  neither tag (e.g. it predates this instrumentation) is bucketed as
  ``endpoint_kind: "attribution_unknown"`` rather than assigned a guessed
  endpoint. Sources with no finer-grained endpoint concept at all (Remotive,
  HN) are ``endpoint_kind: "whole_feed"``.
- Every count below is anchored to snapshot ids so it can always be traced
  back to specific postings (acceptance criterion: "counts reconcile to a
  discovery run and can be traced to snapshot IDs").
- Stage counts are POSTING counts (dedup across the up-to-five per-target
  evaluations a posting can have in one run), except where noted, so the
  funnel is monotonically non-increasing and visually honest. A posting
  clears "target-routed" if at least one of its evaluations routed to a
  target; it clears "A/B" if at least one evaluation reached tier A or B.
  This deliberately differs from ``SourceQueryRun.routed_pairs`` /
  ``viable_evaluations``, which count (posting, target) PAIRS for the
  query-arm scheduler -- see ``src.orchestration.portfolio_run`` for why
  those are pair-level while this report is posting-level.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.models import (
    DiscoveryRun,
    DiscoveryRunEvaluation,
    JobEvaluationReason,
    JobPosting,
    JobSnapshot,
    JobTargetEvaluation,
    PortfolioDecision,
    PortfolioRun,
    SourceEndpoint,
    SourceEndpointRun,
    SourceQueryArm,
    SourceQueryRun,
    TENANT_DEFAULT,
)

_ATS_ADAPTERS = (
    "greenhouse",
    "lever",
    "ashby",
    "workday",
    "smartrecruiters",
    "workable",
    "recruitee",
)
# An endpoint that has returned this many or more fetched postings in the run
# but converted zero to target-routed or zero to full-JD is a visible,
# labeled low-yield source -- not just a quiet zero in a table cell.
_LOW_YIELD_MIN_FETCHED = 5


def _endpoint_key(source: str, raw_data: dict | None) -> tuple[str, str | None, str]:
    """SUP-01B: attribution is exact or explicitly unknown, never guessed.

    ``source_endpoint_key`` / ``source_query_term`` are set once, at fetch
    time, by the code that actually knows which board/arm produced a job
    (``src.intake.search._fetch_board``, ``src.application.jobs``) and
    persisted onto ``JobSnapshot.raw_data`` -- this reads that tag back
    rather than re-deriving identity from company name after the fact.
    """
    raw_data = raw_data or {}
    query_term = raw_data.get("source_query_term")
    if query_term:
        geography = raw_data.get("source_query_location") or ""
        label = f"{query_term} / {geography}" if geography else str(query_term)
        return source, label, "aggregator_query_arm"
    endpoint_key = raw_data.get("source_endpoint_key")
    if endpoint_key:
        return source, str(endpoint_key), "employer_board"
    if source in _ATS_ADAPTERS:
        # A multi-endpoint adapter with no fetch-time tag on this posting --
        # e.g. it predates SUP-01B instrumentation. Conservative per req #5:
        # bucket as unknown rather than guess a company/endpoint.
        return source, None, "attribution_unknown"
    return source, None, "whole_feed"


def _empty_funnel() -> dict[str, int]:
    return {
        "fetched": 0,
        "unique": 0,
        "in_policy_geography": 0,
        "target_routed": 0,
        "full_jd": 0,
        "ab": 0,
        "surfaced": 0,
    }


def _empty_completeness() -> dict[str, int]:
    return {"full": 0, "partial": 0, "snippet": 0, "missing": 0}


def source_funnel_report(
    session: Session,
    *,
    run_id: uuid.UUID | str | None = None,
    tenant_id: str = TENANT_DEFAULT,
    yield_windows_days: tuple[int, ...] = (7, 30),
) -> dict[str, Any]:
    """One row per source/endpoint for a discovery run's supply funnel.

    Defaults to the latest run, matching ``latest_job_pool_report``. Pass
    ``run_id`` to reconcile a specific historical run.
    """

    if run_id is not None:
        run = session.get(DiscoveryRun, run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(str(run_id)))
    else:
        run = session.scalar(
            select(DiscoveryRun)
            .where(DiscoveryRun.tenant_id == tenant_id)
            .order_by(DiscoveryRun.started_at.desc(), DiscoveryRun.id.desc())
            .limit(1)
        )
    if run is None:
        return {"available": False, "message": "No discovery run found to report on."}

    evaluations = list(
        session.scalars(
            select(JobTargetEvaluation)
            .join(
                DiscoveryRunEvaluation,
                DiscoveryRunEvaluation.evaluation_id == JobTargetEvaluation.id,
            )
            .where(DiscoveryRunEvaluation.discovery_run_id == run.id)
        ).all()
    )
    if not evaluations:
        return {
            "available": True,
            "run": {"id": str(run.id), "started_at": run.started_at.isoformat()},
            "sources": [],
            "totals": _empty_funnel(),
            "message": "This run has no linked evaluations yet.",
        }

    snapshot_ids = {evaluation.snapshot_id for evaluation in evaluations}
    snapshots = {
        snapshot.id: snapshot
        for snapshot in session.scalars(
            select(JobSnapshot).where(JobSnapshot.id.in_(snapshot_ids))
        ).all()
    }
    posting_ids = {snapshot.posting_id for snapshot in snapshots.values()}
    postings = {
        posting.id: posting
        for posting in session.scalars(
            select(JobPosting).where(JobPosting.id.in_(posting_ids))
        ).all()
    }

    reasons = list(
        session.scalars(
            select(JobEvaluationReason).where(
                JobEvaluationReason.evaluation_id.in_({e.id for e in evaluations})
            )
        ).all()
    )
    location_pass_by_eval: dict[uuid.UUID, bool] = {}
    routed_by_eval: dict[uuid.UUID, bool] = {}
    for reason in reasons:
        if reason.stage == "global_eligibility" and (reason.details or {}).get("gate_id") == "location":
            location_pass_by_eval[reason.evaluation_id] = reason.decision == "pass"
        if reason.stage == "target_routing":
            routed_by_eval[reason.evaluation_id] = reason.decision == "pass"

    portfolio_run = session.scalar(
        select(PortfolioRun)
        .where(PortfolioRun.discovery_run_id == run.id)
        .order_by(PortfolioRun.started_at.desc())
        .limit(1)
    )
    selected_eval_ids: set[uuid.UUID] = set()
    if portfolio_run is not None:
        selected_eval_ids = set(
            session.scalars(
                select(PortfolioDecision.evaluation_id).where(
                    PortfolioDecision.portfolio_run_id == portfolio_run.id,
                    PortfolioDecision.selected.is_(True),
                )
            ).all()
        )

    # ---- Roll per-posting stage flags up from its per-target evaluations ----
    evals_by_posting: dict[uuid.UUID, list[JobTargetEvaluation]] = defaultdict(list)
    for evaluation in evaluations:
        snapshot = snapshots.get(evaluation.snapshot_id)
        if snapshot is None:
            continue
        evals_by_posting[snapshot.posting_id].append(evaluation)

    groups: dict[tuple[str, str | None], dict[str, Any]] = {}

    def _group(source: str, endpoint: str | None, kind: str) -> dict[str, Any]:
        key = (source, endpoint)
        if key not in groups:
            groups[key] = {
                "source": source,
                "endpoint": endpoint,
                "endpoint_kind": kind,
                "funnel": _empty_funnel(),
                "description_completeness": _empty_completeness(),
                "snapshot_ids": [],
                "posting_ids": set(),
            }
        return groups[key]

    for posting_id, posting_evals in evals_by_posting.items():
        posting = postings.get(posting_id)
        if posting is None:
            continue
        first_snapshot = snapshots.get(posting_evals[0].snapshot_id)
        source, endpoint, kind = _endpoint_key(
            posting.source, (first_snapshot.raw_data or {}) if first_snapshot else {}
        )
        group = _group(source, endpoint, kind)
        funnel = group["funnel"]
        funnel["fetched"] += 1
        funnel["unique"] += 1  # each posting_id is already deduped by construction
        group["posting_ids"].add(posting_id)

        if any(location_pass_by_eval.get(e.id, False) for e in posting_evals):
            funnel["in_policy_geography"] += 1
        if any(routed_by_eval.get(e.id, False) for e in posting_evals):
            funnel["target_routed"] += 1

        # Description completeness / full-JD is a posting property (same
        # underlying text for every target), so read it once.
        completeness = (posting_evals[0].posting_assessment or {}).get("description_completeness")
        if completeness in group["description_completeness"]:
            group["description_completeness"][completeness] += 1
        if completeness == "full":
            funnel["full_jd"] += 1

        if any(e.tier in {"A", "B"} for e in posting_evals):
            funnel["ab"] += 1
        if any(e.id in selected_eval_ids for e in posting_evals):
            funnel["surfaced"] += 1

        for e in posting_evals:
            snap_id = str(e.snapshot_id)
            if snap_id not in group["snapshot_ids"]:
                group["snapshot_ids"].append(snap_id)

    # ---- Attach whatever real fetch telemetry exists for this run ----
    endpoint_runs = list(
        session.scalars(
            select(SourceEndpointRun).where(SourceEndpointRun.discovery_run_id == run.id)
        ).all()
    )
    endpoint_by_id = {
        endpoint.id: endpoint
        for endpoint in session.scalars(
            select(SourceEndpoint).where(
                SourceEndpoint.id.in_({row.endpoint_id for row in endpoint_runs})
            )
        ).all()
    } if endpoint_runs else {}
    query_runs = list(
        session.scalars(
            select(SourceQueryRun).where(SourceQueryRun.discovery_run_id == run.id)
        ).all()
    )
    arm_by_id = {
        arm.id: arm
        for arm in session.scalars(
            select(SourceQueryArm).where(
                SourceQueryArm.id.in_({row.query_arm_id for row in query_runs})
            )
        ).all()
    } if query_runs else {}

    fetch_by_group: dict[tuple[str, str | None], dict[str, Any]] = {}
    for row in endpoint_runs:
        endpoint = endpoint_by_id.get(row.endpoint_id)
        if endpoint is None:
            continue
        key = (endpoint.adapter, endpoint.employer_id and str(endpoint.employer_id) or endpoint.endpoint_key)
        fetch_by_group[key] = {
            "instrumented": True,
            "duration_ms": row.duration_ms,
            "fetched_provider_records": row.provider_records,
            "normalized_records": row.normalized_records,
            "malformed_records": row.malformed_records,
            "last_success_at": endpoint.last_success_at.isoformat() if endpoint.last_success_at else None,
            "endpoint_state": endpoint.state,
        }
    for row in query_runs:
        arm = arm_by_id.get(row.query_arm_id)
        if arm is None:
            continue
        geography = arm.geography or ""
        label = f"{arm.query} / {geography}" if geography else arm.query
        key = (arm.adapter, label)
        existing = fetch_by_group.get(key)
        entry = {
            "instrumented": True,
            "duration_ms": None,
            "fetched_provider_records": row.provider_records,
            "normalized_records": row.unique_postings,
            "malformed_records": None,
            "last_success_at": row.finished_at.isoformat() if row.finished_at else None,
            "endpoint_state": arm.state,
        }
        if existing:
            existing.update({k: v for k, v in entry.items() if v is not None})
        else:
            fetch_by_group[key] = entry

    rows: list[dict[str, Any]] = []
    for (source, endpoint), group in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        funnel = group["funnel"]
        fetch_info = fetch_by_group.get((source, endpoint)) or {
            "instrumented": False,
            "duration_ms": None,
            "fetched_provider_records": None,
            "normalized_records": None,
            "malformed_records": None,
            "last_success_at": None,
            "endpoint_state": None,
        }
        # When real provider-record telemetry exists it is the more precise
        # "fetched" number (it counts raw records before this run's
        # normalization, which can be higher than the postings we ended up
        # evaluating); otherwise fall back to the evaluated-posting count.
        fetched_precise = fetch_info.get("fetched_provider_records")
        fetched = fetched_precise if fetched_precise is not None else funnel["fetched"]
        # ``fetched`` is provider telemetry (the entire board/query result),
        # while ``unique`` is deliberately later in the pipeline: it only
        # includes postings that reached V2 evaluation after search narrowing
        # and identity reconciliation.  Their difference is therefore useful
        # diagnostic attrition, but is *not* a duplicate count.  Calling it
        # "duplicates" made a healthy keyword/location filter look like a
        # broken deduper and obscured where supply was actually disappearing.
        after_fetch_attrition = max(0, fetched - funnel["unique"])
        low_yield = fetched >= _LOW_YIELD_MIN_FETCHED and (
            funnel["target_routed"] == 0 or funnel["full_jd"] == 0
        )
        rows.append(
            {
                "source": source,
                "endpoint": endpoint,
                "endpoint_kind": group["endpoint_kind"],
                "funnel": {**funnel, "fetched": fetched},
                "after_fetch_attrition": after_fetch_attrition,
                "after_fetch_attrition_note": (
                    "Includes search filtering, normalization rejects, and "
                    "identity reconciliation; it is not a duplicate count."
                ),
                "description_completeness": group["description_completeness"],
                "fetch": fetch_info,
                "low_yield": low_yield,
                "snapshot_ids": group["snapshot_ids"][:50],
            }
        )

    totals = _empty_funnel()
    for row in rows:
        for key, value in row["funnel"].items():
            totals[key] += value

    yields = _rolling_ab_yield(session, tenant_id=tenant_id, windows_days=yield_windows_days)
    for row in rows:
        row["yield"] = yields.get((row["source"], row["endpoint"]), {
            f"{days}d_unique_ab": 0 for days in yield_windows_days
        })

    return {
        "available": True,
        "run": {
            "id": str(run.id),
            "mode": run.mode,
            "pipeline_version": run.pipeline_version,
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        },
        "sources": rows,
        "totals": totals,
    }


def _rolling_ab_yield(
    session: Session,
    *,
    tenant_id: str,
    windows_days: tuple[int, ...],
) -> dict[tuple[str, str | None], dict[str, int]]:
    """Trailing N-day unique Tier A/B postings per source/endpoint.

    Independent of any single run -- looks across every immutable evaluation
    ever created, so a source's yield reflects its actual track record, not
    just whatever happened to be in the most recent run.
    """

    cutoff = datetime.now(UTC) - timedelta(days=max(windows_days))
    evaluations = list(
        session.scalars(
            select(JobTargetEvaluation).where(
                JobTargetEvaluation.tenant_id == tenant_id,
                JobTargetEvaluation.tier.in_(("A", "B")),
                JobTargetEvaluation.created_at >= cutoff,
            )
        ).all()
    )
    if not evaluations:
        return {}
    snapshot_ids = {e.snapshot_id for e in evaluations}
    snapshots = {
        s.id: s
        for s in session.scalars(select(JobSnapshot).where(JobSnapshot.id.in_(snapshot_ids))).all()
    }
    posting_ids = {s.posting_id for s in snapshots.values()}
    postings = {
        p.id: p
        for p in session.scalars(select(JobPosting).where(JobPosting.id.in_(posting_ids))).all()
    }

    seen: dict[tuple[str, str | None], dict[int, set[uuid.UUID]]] = defaultdict(
        lambda: {days: set() for days in windows_days}
    )
    now = datetime.now(UTC)
    for evaluation in evaluations:
        snapshot = snapshots.get(evaluation.snapshot_id)
        if snapshot is None:
            continue
        posting = postings.get(snapshot.posting_id)
        if posting is None:
            continue
        source, endpoint, _kind = _endpoint_key(posting.source, snapshot.raw_data or {})
        age_days = (now - evaluation.created_at).days
        for days in windows_days:
            if age_days <= days:
                seen[(source, endpoint)][days].add(posting.id)

    return {
        key: {f"{days}d_unique_ab": len(ids) for days, ids in windows.items()}
        for key, windows in seen.items()
    }


_YIELD_DEMOTION_LOOKBACK_RUNS = 7
_YIELD_DEMOTION_MIN_RUNS = 3  # Phase S6 exploration budget, applied here too


def compute_yield_demotion_candidates(
    session: Session,
    *,
    tenant_id: str = TENANT_DEFAULT,
    lookback_runs: int = _YIELD_DEMOTION_LOOKBACK_RUNS,
) -> list[dict[str, Any]]:
    """SUP-09 (Phase S6): direct-ATS endpoints whose recent non-empty fetches
    routed nothing to any target.

    This is a **read-only recommendation**, not an automatic state mutation.
    Unlike the fetch-health quarantine ``src.jobs.source_endpoints`` now
    drives automatically (SUP-09's other half -- a fetch either succeeds or
    it doesn't, a symmetric and already-automatic signal), "routes nothing"
    depends on target-matching/scoring logic downstream of the fetch. If
    that logic has a bug, auto-demoting the endpoint would silently starve a
    target of real supply while masking the actual defect -- exactly the
    failure mode the rest of this codebase has been careful never to create
    silently (see SUP-02's "propose, never auto-enable" precedent). Surface
    it for review; a human (or a future, separately-reviewed ticket) decides
    whether to act on it.

    An endpoint qualifies when it has at least ``lookback_runs`` *non-empty*
    ``SourceEndpointRun`` rows (fetch actually returned postings) and every
    one of its most recent ``lookback_runs`` non-empty runs routed zero
    postings to any target. Endpoints with fewer than
    ``_YIELD_DEMOTION_MIN_RUNS`` total runs are excluded entirely (same
    three-run exploration budget as the fetch-health path) -- a new
    endpoint with two non-empty-but-unrouted runs is not yet evidence of
    anything.
    """
    endpoints = list(
        session.scalars(
            select(SourceEndpoint).where(
                SourceEndpoint.tenant_id == tenant_id,
                SourceEndpoint.adapter.in_(_ATS_ADAPTERS),
            )
        ).all()
    )
    if not endpoints:
        return []

    candidates: list[dict[str, Any]] = []
    for endpoint in endpoints:
        total_runs = (
            session.scalar(
                select(func.count())
                .select_from(SourceEndpointRun)
                .where(SourceEndpointRun.endpoint_id == endpoint.id)
            )
            or 0
        )
        if total_runs < _YIELD_DEMOTION_MIN_RUNS:
            continue

        recent_nonempty_runs = list(
            session.scalars(
                select(SourceEndpointRun)
                .where(
                    SourceEndpointRun.endpoint_id == endpoint.id,
                    SourceEndpointRun.normalized_records > 0,
                )
                .order_by(SourceEndpointRun.started_at.desc())
                .limit(lookback_runs)
            ).all()
        )
        if len(recent_nonempty_runs) < lookback_runs:
            continue  # not enough non-empty history yet to judge

        run_ids = {run.id for run in recent_nonempty_runs}
        snapshot_rows = list(
            session.scalars(
                select(JobSnapshot).where(JobSnapshot.source_endpoint_run_id.in_(run_ids))
            ).all()
        )
        if not snapshot_rows:
            # Non-empty fetches exist but nothing was ever persisted as a
            # snapshot from them -- can't distinguish "genuinely unrouted"
            # from "attribution gap predating this instrumentation".
            # Conservative: don't flag what can't be traced.
            continue

        snapshot_by_run: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        for snap in snapshot_rows:
            if snap.source_endpoint_run_id is not None:
                snapshot_by_run[snap.source_endpoint_run_id].append(snap.id)

        if set(snapshot_by_run) != run_ids:
            # At least one of the lookback runs has no traceable snapshot
            # at all -- same conservative reasoning as above.
            continue

        all_snapshot_ids = {sid for ids in snapshot_by_run.values() for sid in ids}
        evaluations = list(
            session.scalars(
                select(JobTargetEvaluation).where(
                    JobTargetEvaluation.snapshot_id.in_(all_snapshot_ids)
                )
            ).all()
        )
        routed_snapshot_ids: set[uuid.UUID] = set()
        if evaluations:
            reasons = list(
                session.scalars(
                    select(JobEvaluationReason).where(
                        JobEvaluationReason.evaluation_id.in_({e.id for e in evaluations}),
                        JobEvaluationReason.stage == "target_routing",
                        JobEvaluationReason.decision == "pass",
                    )
                ).all()
            )
            routed_eval_ids = {r.evaluation_id for r in reasons}
            routed_snapshot_ids = {
                e.snapshot_id for e in evaluations if e.id in routed_eval_ids
            }

        any_run_routed = any(
            any(sid in routed_snapshot_ids for sid in ids)
            for ids in snapshot_by_run.values()
        )
        if any_run_routed:
            continue

        candidates.append(
            {
                "adapter": endpoint.adapter,
                "endpoint_key": endpoint.endpoint_key,
                "endpoint_id": str(endpoint.id),
                "state": endpoint.state,
                "lookback_runs": lookback_runs,
                "total_runs": total_runs,
                "reason": "zero_routed_across_recent_nonempty_runs",
            }
        )

    return candidates


__all__ = ["compute_yield_demotion_candidates", "source_funnel_report"]
