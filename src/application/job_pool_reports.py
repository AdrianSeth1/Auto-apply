"""Nontechnical audit views over the immutable Job Pool V2 ledger."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
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
    TENANT_DEFAULT,
)
from src.matching.scorer_v2 import SCORER_VERSION

_EXPLORATION_TITLE_BLOCKLIST = (
    "india",
    "mumbai",
    "israel",
    "workday",
    "oracle",
    "sap",
    "s/4hana",
    "clinical",
    "identity management",
    "privileged access",
)


def latest_job_pool_report(session: Session, *, tenant_id: str = TENANT_DEFAULT) -> dict[str, Any]:
    run = session.scalar(
        select(DiscoveryRun)
        .where(DiscoveryRun.tenant_id == tenant_id)
        .order_by(DiscoveryRun.started_at.desc(), DiscoveryRun.id.desc())
        .limit(1)
    )
    health = Counter(
        session.scalars(
            select(SourceEndpoint.state).where(SourceEndpoint.tenant_id == tenant_id)
        ).all()
    )
    if run is None:
        return {
            "available": False,
            "message": "No V2 shadow run has completed yet.",
            "endpoint_health": dict(sorted(health.items())),
        }

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
    reasons = list(
        session.scalars(
            select(JobEvaluationReason).where(
                JobEvaluationReason.evaluation_id.in_({evaluation.id for evaluation in evaluations})
            )
        ).all()
    )
    portfolio_run = session.scalar(
        select(PortfolioRun)
        .where(PortfolioRun.discovery_run_id == run.id)
        .order_by(PortfolioRun.started_at.desc())
        .limit(1)
    )
    decisions = (
        list(
            session.scalars(
                select(PortfolioDecision).where(
                    PortfolioDecision.portfolio_run_id == portfolio_run.id
                )
            ).all()
        )
        if portfolio_run
        else []
    )
    snapshots = {
        snapshot.id: snapshot
        for snapshot in session.scalars(
            select(JobSnapshot).where(
                JobSnapshot.id.in_({evaluation.snapshot_id for evaluation in evaluations})
            )
        ).all()
    } if evaluations else {}
    postings = {
        posting.id: posting
        for posting in session.scalars(
            select(JobPosting).where(
                JobPosting.id.in_({snapshot.posting_id for snapshot in snapshots.values()})
            )
        ).all()
    } if snapshots else {}

    tiers: dict[str, Counter] = defaultdict(Counter)
    stages: dict[str, Counter] = defaultdict(Counter)
    missing: list[dict[str, Any]] = []
    for evaluation in evaluations:
        tiers[evaluation.target_id][evaluation.tier] += 1
        stages[evaluation.target_id][evaluation.stage_status] += 1
        missing_fields = [
            key
            for key, value in (evaluation.component_confidence or {}).items()
            if value is None or float(value) <= 0
        ]
        if evaluation.tier == "unresolved" or missing_fields:
            snapshot = snapshots.get(evaluation.snapshot_id)
            posting = postings.get(snapshot.posting_id) if snapshot else None
            missing.append(
                {
                    "evaluation_id": str(evaluation.id),
                    "target": evaluation.target_id,
                    "company": posting.company if posting else "Unknown employer",
                    "title": snapshot.title if snapshot else "Unknown role",
                    "missing": missing_fields or ["evaluation unresolved"],
                }
            )

    # One condition can be represented by both its terminal gate row and its
    # explanatory gap row. Count distinct evaluations so the operator funnel
    # never claims more losses than evaluated pairs.
    evaluations_by_reason: dict[str, set] = defaultdict(set)
    route_by_evaluation: dict[Any, str] = {}
    for reason in reasons:
        if reason.decision in {"fail", "defer"}:
            evaluations_by_reason[reason.reason_code].add(reason.evaluation_id)
        if reason.stage == "target_routing":
            route_by_evaluation[reason.evaluation_id] = reason.reason_code
    reason_counts = Counter(
        {reason: len(evaluation_ids) for reason, evaluation_ids in evaluations_by_reason.items()}
    )
    selected = [decision for decision in decisions if decision.selected]
    reserved = [decision for decision in selected if decision.review_id is not None]
    decision_rows = []
    evaluation_by_id = {evaluation.id: evaluation for evaluation in evaluations}
    for decision in sorted(selected, key=lambda value: (value.rank or 9999, str(value.id))):
        evaluation = evaluation_by_id.get(decision.evaluation_id)
        snapshot = snapshots.get(evaluation.snapshot_id) if evaluation else None
        posting = postings.get(snapshot.posting_id) if snapshot else None
        explanation = (evaluation.explanation or {}) if evaluation else {}
        decision_rows.append(
            {
                "company": posting.company if posting else "Unknown employer",
                "title": snapshot.title if snapshot else "Unknown role",
                "target": decision.owned_target_id,
                "tier": evaluation.tier if evaluation else "unknown",
                "lane": decision.lane,
                "review_index": round(evaluation.adjusted_review_index, 1) if evaluation else None,
                "reserved": decision.review_id is not None,
                "why": decision.reason_codes or [],
                "strengths": explanation.get("strengths", [])[:3],
                "risks": explanation.get("gaps", [])[:3],
            }
        )

    selected_snapshot_ids = {
        evaluation_by_id[decision.evaluation_id].snapshot_id
        for decision in selected
        if decision.evaluation_id in evaluation_by_id
    }
    promising = []
    for evaluation in sorted(
        evaluations,
        key=lambda value: (-value.adjusted_review_index, value.target_id, str(value.id)),
    ):
        if evaluation.tier != "C" or evaluation.snapshot_id in selected_snapshot_ids:
            continue
        if any(gate.get("status") == "fail" for gate in (evaluation.gate_results or [])):
            continue
        scores = evaluation.component_scores or {}
        if (
            scores.get("role", 0) < 60
            or scores.get("level", 0) < 45
            or scores.get("evidence", 0) < 50
            or scores.get("domain", 0) < 45
        ):
            continue
        route = route_by_evaluation.get(evaluation.id, "")
        if route in {"route_unmatched", "route_excluded", "route_description_only"}:
            continue
        snapshot = snapshots.get(evaluation.snapshot_id)
        posting = postings.get(snapshot.posting_id) if snapshot else None
        identity_text = f"{snapshot.title if snapshot else ''} {snapshot.location if snapshot else ''}".casefold()
        if any(term in identity_text for term in _EXPLORATION_TITLE_BLOCKLIST):
            continue
        gaps = (evaluation.explanation or {}).get("gaps", [])
        promising.append(
            {
                "evaluation_id": str(evaluation.id),
                "company": posting.company if posting else "Unknown employer",
                "title": snapshot.title if snapshot else "Unknown role",
                "target": evaluation.target_id,
                "review_index": round(evaluation.adjusted_review_index, 1),
                "confidence": round(evaluation.confidence, 2),
                "why_not_ab": [gap.get("reason_code") for gap in gaps][:4],
                "exploration_only": True,
            }
        )
        if len(promising) >= 15:
            break

    return {
        "available": True,
        "run": {
            "id": str(run.id),
            "mode": run.mode,
            "pipeline_version": run.pipeline_version,
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "shadow": run.mode == "v2_shadow" or run.pipeline_version == "v2_shadow",
            "scorer_version": evaluations[0].scorer_version if evaluations else None,
            "current_scorer_version": SCORER_VERSION,
            "version_current": bool(evaluations)
            and all(evaluation.scorer_version == SCORER_VERSION for evaluation in evaluations),
        },
        "counts": {
            "retrieved_unique": int(
                (run.counts or {}).get("filtered_total")
                or (run.counts or {}).get("raw_total")
                or (run.counts or {}).get("total")
                or 0
            ),
            "evaluated_pairs": len(evaluations),
            "portfolio_attempts": len(selected),
            "successful_cards": len(reserved),
            "unresolved_or_missing": len(missing),
        },
        "target_supply": {
            target: {"tiers": dict(sorted(tier.items())), "stages": dict(sorted(stages[target].items()))}
            for target, tier in sorted(tiers.items())
        },
        "loss_reasons": [
            {"reason": reason, "count": count}
            for reason, count in reason_counts.most_common()
        ],
        "endpoint_health": dict(sorted(health.items())),
        "proposed_cards": decision_rows,
        "promising_near_misses": promising,
        "unresolved": missing[:100],
    }


__all__ = ["latest_job_pool_report"]
