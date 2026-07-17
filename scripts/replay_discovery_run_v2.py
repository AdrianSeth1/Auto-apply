"""Read-only replay of a persisted discovery snapshot with current V2 versions."""

from __future__ import annotations

import argparse
import json
import uuid

from sqlalchemy import select

from src.core.database import get_session_factory
from src.core.models import (
    DiscoveryRunEvaluation,
    JobPosting,
    JobSnapshot,
    JobTargetEvaluation,
    PortfolioDecision,
    ReviewQueueEntry,
)
from src.matching.profile_v2 import load_candidate, load_targets, resolve_target
from src.matching.scorer_v2 import evaluate_job_target
from src.orchestration.portfolio import PortfolioCandidateV2, load_portfolio_policy, select_portfolio
from src.orchestration.portfolio_run import raw_job_from_payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id", type=uuid.UUID)
    parser.add_argument("--seed", default="read-only-replay")
    args = parser.parse_args()
    candidate = load_candidate()
    resolved = [resolve_target(candidate, target) for target in load_targets()]
    factory = get_session_factory()
    candidates: list[PortfolioCandidateV2] = []
    with factory() as session:
        snapshot_ids = set(
            session.scalars(
                select(JobTargetEvaluation.snapshot_id)
                .join(
                    DiscoveryRunEvaluation,
                    DiscoveryRunEvaluation.evaluation_id == JobTargetEvaluation.id,
                )
                .where(DiscoveryRunEvaluation.discovery_run_id == args.run_id)
            ).all()
        )
        snapshots = session.scalars(select(JobSnapshot).where(JobSnapshot.id.in_(snapshot_ids))).all()
        postings = {
            posting.id: posting
            for posting in session.scalars(
                select(JobPosting).where(
                    JobPosting.id.in_({snapshot.posting_id for snapshot in snapshots})
                )
            ).all()
        }
        excluded = set(
            session.scalars(
                select(ReviewQueueEntry.job_snapshot_id).where(
                    ReviewQueueEntry.job_snapshot_id.in_(snapshot_ids),
                    ReviewQueueEntry.status.in_(("pending", "approved", "submitted")),
                )
            ).all()
        )
        excluded_groups = set(
            session.scalars(
                select(PortfolioDecision.canonical_group).where(
                    PortfolioDecision.tenant_id == "default",
                    PortfolioDecision.selected.is_(True),
                )
            ).all()
        )
        for snapshot in snapshots:
            posting = postings[snapshot.posting_id]
            job = raw_job_from_payload(
                {
                    "source": posting.source,
                    "source_id": posting.source_id,
                    "company": posting.company,
                    "title": snapshot.title,
                    "location": snapshot.location,
                    "employment_type": snapshot.employment_type or "unknown",
                    "seniority": snapshot.seniority or "unknown",
                    "description": snapshot.description,
                    "requirements": snapshot.requirements or {},
                    "application_url": snapshot.application_url,
                    "raw_data": snapshot.raw_data or {},
                    "provenance": snapshot.provenance,
                }
            )
            for target in resolved:
                evaluation = evaluate_job_target(job, target)
                candidates.append(
                    PortfolioCandidateV2(
                        evaluation_id=f"replay:{snapshot.id}:{target.target.id}",
                        snapshot_id=str(snapshot.id),
                        occurrence_key=f"{posting.source}::{posting.source_id}",
                        source=posting.source,
                        source_id=posting.source_id,
                        company=posting.company,
                        title=snapshot.title,
                        location=snapshot.location,
                        application_url=snapshot.application_url,
                        canonical_group=posting.canonical_fingerprint,
                        target_priority=target.target.priority,
                        evaluation=evaluation,
                        history_excluded=(
                            snapshot.id in excluded
                            or posting.canonical_fingerprint in excluded_groups
                        ),
                        history_reason=(
                            "existing_review_journey"
                            if snapshot.id in excluded
                            else "previously_surfaced_portfolio"
                            if posting.canonical_fingerprint in excluded_groups
                            else None
                        ),
                    )
                )
    result = select_portfolio(candidates, policy=load_portfolio_policy(), seed=args.seed)
    selected = [
        {
            "company": item.company,
            "title": item.title,
            "target": item.evaluation.target_id,
            "tier": item.evaluation.tier,
            "review_index": item.evaluation.adjusted_review_index,
            "startup": item.startup,
            "lane": "core" if item in result.selected_core else "startup_bonus",
        }
        for item in (*result.selected_core, *result.selected_startup_bonus)
    ]
    print(json.dumps({"run_id": str(args.run_id), "read_only": True, "counts": result.counts, "selected": selected}, indent=2))


if __name__ == "__main__":
    main()
