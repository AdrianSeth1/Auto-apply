"""Idempotent persistence for immutable Job Pool V2 evaluations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.models import (
    DiscoveryRunEvaluation,
    JobEvaluationReason,
    JobTargetEvaluation,
    TENANT_DEFAULT,
)
from src.matching.job_facts import JobFactsV2, aggregate_gate_status
from src.matching.scorer_v2 import JobTargetEvaluationV2


@dataclass(frozen=True)
class EvaluationWriteResult:
    evaluation: JobTargetEvaluation
    created: bool


def reason_rows_for_evaluation(
    result: JobTargetEvaluationV2,
) -> list[dict[str, Any]]:
    """Produce normalized, aggregatable reason rows for every terminal stage."""

    rows: list[dict[str, Any]] = []
    for gate in result.gate_results:
        rows.append(
            {
                "stage": "global_eligibility",
                "decision": gate.status,
                "reason_code": gate.reason_code,
                "severity": "blocking" if gate.status == "fail" else "info",
                "evidence": list(gate.job_evidence),
                "details": {"gate_id": gate.gate_id, "message": gate.message},
            }
        )
    route_pass = result.route_tier != "unmatched" and result.component_scores["role"] >= 35
    rows.append(
        {
            "stage": "target_routing",
            "decision": "pass" if route_pass else "fail",
            "reason_code": f"route_{result.route_tier}",
            "severity": "info" if route_pass else "blocking",
            "evidence": [],
            "details": {"role_score": result.component_scores["role"]},
        }
    )
    rows.append(
        {
            "stage": "target_candidacy",
            "decision": "pass" if result.tier in {"A", "B", "C"} else "fail",
            "reason_code": f"tier_{result.tier.lower()}",
            "severity": "info" if result.tier in {"A", "B"} else "warning",
            "evidence": [],
            "details": {
                "story_fit": result.story_fit,
                "candidacy_index": result.candidacy_index,
                "review_index": result.review_index,
                "confidence": result.confidence,
            },
        }
    )
    for gap in result.gaps:
        rows.append(
            {
                "stage": "target_candidacy",
                "decision": "fail" if gap.severity == "blocking" else "defer",
                "reason_code": gap.reason_code,
                "severity": gap.severity,
                "evidence": [gap.job_excerpt] if gap.job_excerpt else [],
                "details": {
                    "message": gap.message,
                    "missing_capability_ids": list(gap.missing_capability_ids),
                },
            }
        )
    return rows


def persist_evaluation(
    session: Session,
    *,
    snapshot_id: uuid.UUID,
    facts: JobFactsV2,
    result: JobTargetEvaluationV2,
    discovery_run_id: uuid.UUID | None = None,
    tenant_id: str = TENANT_DEFAULT,
) -> EvaluationWriteResult:
    """Insert once for the full version tuple; identical replays reuse the row."""

    role_taxonomy_version, capability_taxonomy_version = result.taxonomy_versions
    key = {
        "tenant_id": tenant_id,
        "snapshot_id": snapshot_id,
        "target_id": result.target_id,
        "candidate_version": result.candidate_version,
        "target_version": result.target_version,
        "parser_version": result.parser_version,
        "role_taxonomy_version": role_taxonomy_version,
        "capability_taxonomy_version": capability_taxonomy_version,
        "scorer_version": result.scorer_version,
    }
    existing = session.scalar(select(JobTargetEvaluation).filter_by(**key))
    if existing is not None:
        _link_run(
            session,
            evaluation_id=existing.id,
            discovery_run_id=discovery_run_id,
            tenant_id=tenant_id,
        )
        return EvaluationWriteResult(evaluation=existing, created=False)

    gate_status = aggregate_gate_status(result.gate_results)
    row = JobTargetEvaluation(
        **key,
        discovery_run_id=discovery_run_id,
        model_version="deterministic",
        pipeline_version=result.pipeline_version,
        stage_status="failed" if gate_status == "fail" else "unresolved" if gate_status == "unknown" else "evaluated",
        facts=facts.model_dump(mode="json"),
        gate_results=[gate.model_dump(mode="json") for gate in result.gate_results],
        component_scores=result.component_scores,
        component_confidence=result.component_confidence,
        story_fit=result.story_fit,
        candidacy_index=result.candidacy_index,
        review_index=result.review_index,
        adjusted_review_index=result.adjusted_review_index,
        tier=result.tier,
        confidence=result.confidence,
        explanation={
            "strengths": [item.model_dump(mode="json") for item in result.strengths],
            "gaps": [item.model_dump(mode="json") for item in result.gaps],
            "missing_critical_facts": list(result.missing_critical_facts),
            "feedback_adjustment": result.feedback_adjustment.model_dump(mode="json"),
        },
        employer_assessment=result.employer_assessment.model_dump(mode="json"),
        posting_assessment=result.posting_assessment.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    _link_run(
        session,
        evaluation_id=row.id,
        discovery_run_id=discovery_run_id,
        tenant_id=tenant_id,
    )
    for reason in reason_rows_for_evaluation(result):
        session.add(
            JobEvaluationReason(
                tenant_id=tenant_id,
                evaluation_id=row.id,
                discovery_run_id=discovery_run_id,
                target_id=result.target_id,
                **reason,
            )
        )
    session.flush()
    return EvaluationWriteResult(evaluation=row, created=True)


def _link_run(
    session: Session,
    *,
    evaluation_id: uuid.UUID,
    discovery_run_id: uuid.UUID | None,
    tenant_id: str,
) -> None:
    if discovery_run_id is None:
        return
    existing = session.scalar(
        select(DiscoveryRunEvaluation.id).where(
            DiscoveryRunEvaluation.discovery_run_id == discovery_run_id,
            DiscoveryRunEvaluation.evaluation_id == evaluation_id,
        )
    )
    if existing is None:
        session.add(
            DiscoveryRunEvaluation(
                tenant_id=tenant_id,
                discovery_run_id=discovery_run_id,
                evaluation_id=evaluation_id,
            )
        )
        session.flush()


__all__ = ["EvaluationWriteResult", "persist_evaluation", "reason_rows_for_evaluation"]
