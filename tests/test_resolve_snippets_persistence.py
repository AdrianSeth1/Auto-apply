"""Postgres-backed verification of Adzuna full-JD recovery persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy import delete, select

from src.application.resolve_snippets import resolve_pending_snippets
from src.core.database import get_session_factory
from src.core.models import Job, JobPosting, JobSnapshot
from src.intake.full_jd_resolver import ResolveOutcome
from src.intake.schema import ApplicationTargetV2, JobProvenanceV2, RawJob


def test_recovery_adds_immutable_snapshot_and_preserves_attempt_audit():
    factory = get_session_factory()
    tenant_id = f"test-resolve-snippets-{uuid.uuid4().hex[:8]}"
    source_id = f"adz-{uuid.uuid4().hex}"
    legacy_id = None
    posting_id = None

    try:
        with factory() as session, session.begin():
            legacy = Job(
                tenant_id=tenant_id,
                source="AdZuNa",
                source_id=source_id,
                company="Acme Corp",
                title="Solutions Engineer",
                location="Remote",
                description="Short discovery snippet",
                application_url="https://www.adzuna.com/details/123",
                raw_data={
                    "description_completeness": "snippet",
                    "full_jd_recovery_attempts": 1,
                },
                discovered_at=datetime.now(UTC),
            )
            posting = JobPosting(
                tenant_id=tenant_id,
                source="ADZUNA",
                source_id=source_id,
                company="Acme Corp",
                state="new",
            )
            session.add_all([legacy, posting])
            session.flush()
            legacy_id = legacy.id
            posting_id = posting.id

        full_description = "Complete employer-provided description. " * 20
        recovered = RawJob(
            source="adzuna",
            source_id=source_id,
            company="Acme Corp",
            title="Solutions Engineer",
            location="Remote",
            description=full_description,
            application_url="https://boards.greenhouse.io/acme/jobs/123",
            raw_data={
                "description_completeness": "full",
                "full_jd_recovered": True,
                "full_jd_source_adapter": "greenhouse",
            },
            provenance=JobProvenanceV2(
                adapter="adzuna",
                channel="aggregator",
                listing_url="https://www.adzuna.com/details/123",
                publisher_relationship="third_party_aggregator",
                description_completeness="full",
                application_target=ApplicationTargetV2(
                    original_url="https://www.adzuna.com/details/123",
                    resolved_url="https://boards.greenhouse.io/acme/jobs/123",
                    kind="direct_ats",
                    resolution_status="resolved_via_adapter",
                ),
                parser_confidence=0.8,
            ),
        )

        with factory() as session, session.begin():
            with (
                patch(
                    "src.application.resolve_snippets._pending_snippets_query",
                    return_value=select(Job).where(Job.id == legacy_id),
                ),
                patch(
                    "src.application.resolve_snippets.resolve_full_jd",
                    return_value=ResolveOutcome(resolved=True, job=recovered),
                ),
            ):
                summary = resolve_pending_snippets(session, source_policy={})

            assert summary.considered == 1
            assert summary.recovered == 1

        with factory() as session:
            legacy = session.get(Job, legacy_id)
            postings = list(
                session.scalars(
                    select(JobPosting).where(
                        JobPosting.tenant_id == tenant_id,
                        JobPosting.source_id == source_id,
                    )
                )
            )
            snapshots = list(
                session.scalars(select(JobSnapshot).where(JobSnapshot.posting_id == posting_id))
            )

            assert legacy.description == full_description
            assert legacy.raw_data["full_jd_recovery_attempts"] == 2
            assert legacy.raw_data["full_jd_recovery_last_reason"] == "resolved"
            assert legacy.raw_data["full_jd_recovery_last_attempted_at"]
            assert len(postings) == 1
            assert postings[0].latest_snapshot_id == snapshots[0].id
            assert len(snapshots) == 1
            assert snapshots[0].description == full_description
            assert snapshots[0].provenance["application_target"]["kind"] == "direct_ats"
    finally:
        if legacy_id is not None or posting_id is not None:
            with factory() as session, session.begin():
                if posting_id is not None:
                    session.execute(delete(JobSnapshot).where(JobSnapshot.posting_id == posting_id))
                    session.execute(delete(JobPosting).where(JobPosting.id == posting_id))
                if legacy_id is not None:
                    session.execute(delete(Job).where(Job.id == legacy_id))
