"""Idempotent data backfills for funnel analytics and duplicate suggestions.

Kept as ordinary application code (rather than opaque migration SQL) so Claude,
operators, and tests can inspect the matching rules and rerun the backfill safely.
"""

from __future__ import annotations

from src.application.funnel import record_event
from src.core.models import Application, Job, JobPosting, JobSnapshot, ReviewQueueEntry
from src.jobs.identity import canonical_fingerprint


def backfill_identity_and_funnel(session) -> dict[str, int]:
    counts = {"legacy_fingerprints": 0, "posting_fingerprints": 0, "funnel_candidates": 0}

    for job in session.query(Job).yield_per(500):
        fingerprint = canonical_fingerprint(
            company=job.company,
            title=job.title,
            location=job.location,
            application_url=job.application_url,
        )
        if fingerprint and job.canonical_fingerprint != fingerprint:
            job.canonical_fingerprint = fingerprint
            counts["legacy_fingerprints"] += 1
        record_event(
            session,
            entity_type="job",
            entity_id=job.id,
            stage="discovered",
            job_id=job.id,
            source=job.source,
            profile_variant=(job.raw_data or {}).get("best_profile"),
            metadata={"backfilled": True, "company": job.company, "title": job.title},
            occurred_at=job.discovered_at,
            tenant_id=job.tenant_id,
        )
        counts["funnel_candidates"] += 1

    posting_rows = (
        session.query(JobPosting, JobSnapshot)
        .join(JobSnapshot, JobPosting.latest_snapshot_id == JobSnapshot.id)
        .yield_per(500)
    )
    for posting, snapshot in posting_rows:
        fingerprint = canonical_fingerprint(
            company=posting.company,
            title=snapshot.title,
            location=snapshot.location,
            application_url=snapshot.application_url or posting.canonical_url,
        )
        if fingerprint and posting.canonical_fingerprint != fingerprint:
            posting.canonical_fingerprint = fingerprint
            counts["posting_fingerprints"] += 1

    for entry in session.query(ReviewQueueEntry).filter(ReviewQueueEntry.reviewed_at.isnot(None)):
        record_event(
            session,
            entity_type="review",
            entity_id=entry.id,
            stage="reviewed",
            job_id=entry.job_id,
            posting_id=entry.job_id if entry.job_snapshot_id else None,
            material_variant=entry.materials_path,
            metadata={"backfilled": True, "decision": entry.decision},
            occurred_at=entry.reviewed_at,
            tenant_id=entry.tenant_id,
        )
        counts["funnel_candidates"] += 1

    for app in session.query(Application).filter(Application.deleted_at.is_(None)):
        common = {
            "entity_type": "application",
            "entity_id": app.id,
            "job_id": app.job_id,
            "application_id": app.id,
            "profile_variant": app.profile_variant,
            "material_variant": app.material_variant,
            "time_spent_seconds": app.time_spent_seconds,
            "tenant_id": app.tenant_id,
        }
        if app.submitted_at:
            record_event(
                session,
                stage="applied",
                metadata={"backfilled": True},
                occurred_at=app.submitted_at,
                **common,
            )
            counts["funnel_candidates"] += 1
        if app.outcome in {"oa", "interview", "offer"}:
            stage = "screen" if app.outcome == "oa" else app.outcome
            record_event(
                session,
                stage=stage,
                metadata={"backfilled": True, "outcome": app.outcome},
                occurred_at=app.outcome_updated_at or app.updated_at,
                **common,
            )
            counts["funnel_candidates"] += 1

    session.commit()
    return counts

