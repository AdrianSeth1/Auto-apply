"""Job intake storage — persist RawJob objects to the database with deduplication."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.models import Job
from src.intake.schema import RawJob

logger = logging.getLogger("autoapply.intake.storage")


def upsert_jobs(session: Session, jobs: list[RawJob]) -> tuple[int, int]:
    """Persist jobs to the database, skipping duplicates.

    Deduplication key: source + company (normalized) + source_id.
    If a job already exists (same key), it is skipped.

    Returns:
        (inserted_count, skipped_count)
    """
    if not jobs:
        return 0, 0

    # Build a set of existing dedup keys to avoid re-querying per job
    existing_keys = _load_existing_keys(session, jobs)

    inserted = 0
    skipped = 0

    for raw in jobs:
        key = raw.dedup_key()
        if key in existing_keys:
            skipped += 1
            continue

        db_job = Job(
            id=raw.id,
            source=raw.source,
            source_id=raw.source_id,
            company=raw.company,
            title=raw.title,
            location=raw.location,
            employment_type=raw.employment_type,
            seniority=raw.seniority,
            description=raw.description,
            requirements=raw.requirements.model_dump(),
            visa_sponsorship=raw.requirements.visa_sponsorship,
            ats_type=raw.ats_type,
            application_url=raw.application_url,
            raw_data=raw.raw_data,
            discovered_at=raw.discovered_at,
            expires_at=raw.expires_at,
        )
        try:
            session.add(db_job)
            session.flush()
            existing_keys.add(key)
            inserted += 1
        except IntegrityError:
            session.rollback()
            logger.debug("Duplicate job skipped on flush: %s", key)
            skipped += 1

    session.commit()
    logger.info("Upserted jobs: %d new, %d skipped", inserted, skipped)
    return inserted, skipped


def _load_existing_keys(session: Session, jobs: list[RawJob]) -> set[str]:
    """Load dedup keys for jobs that might already be in the DB."""
    companies = {j.company.lower() for j in jobs}
    sources = {j.source for j in jobs}

    # NOTE: ``companies`` is lowercased but the DB stores the original
    # casing ("Stripe"), so the comparison must be case-insensitive.
    # A case-sensitive ``Job.company.in_(...)`` here matched nothing,
    # which made every search re-insert the entire board (found as
    # ~21k duplicate rows on 2026-07-01).
    existing = (
        session.query(Job.source, Job.company, Job.source_id)
        .filter(Job.source.in_(sources), func.lower(Job.company).in_(companies))
        .all()
    )

    keys = set()
    for row in existing:
        keys.add(f"{row.source}::{row.company.lower()}::{row.source_id or ''}")

    return keys


def persist_and_sync_ids(session: Session, jobs: list[RawJob]) -> None:
    """Persist jobs to the Job table and update each RawJob.id to the DB row id.

    Unlike upsert_jobs (which skips duplicates and keeps ephemeral RawJob UUIDs),
    this function ensures every returned RawJob carries the *stable* DB id so
    downstream tasks (materials.generate) can look the row up by primary key.

    - New jobs: inserted with their current RawJob.id (which becomes the DB id).
    - Existing jobs: RawJob.id is overwritten with the id already in the DB.
    """
    if not jobs:
        return

    companies = {j.company.lower() for j in jobs}
    sources = {j.source for j in jobs}

    # Case-insensitive company match — see the comment in
    # ``_load_existing_keys`` for the duplicate-explosion bug this fixes.
    existing_rows = (
        session.query(Job.id, Job.source, Job.company, Job.source_id)
        .filter(Job.source.in_(sources), func.lower(Job.company).in_(companies))
        .all()
    )
    key_to_id: dict[str, object] = {
        f"{row.source}::{row.company.lower()}::{row.source_id}": row.id
        for row in existing_rows
    }

    for raw in jobs:
        key = raw.dedup_key()
        if key in key_to_id:
            # Job already persisted — sync the RawJob id to the stable DB id
            raw.id = key_to_id[key]
        else:
            # New job — insert and record so subsequent duplicates in same batch skip cleanly
            db_job = Job(
                id=raw.id,
                source=raw.source,
                source_id=raw.source_id,
                company=raw.company,
                title=raw.title,
                location=raw.location,
                employment_type=raw.employment_type,
                seniority=raw.seniority,
                description=raw.description,
                requirements=raw.requirements.model_dump(),
                visa_sponsorship=raw.requirements.visa_sponsorship,
                ats_type=raw.ats_type,
                application_url=raw.application_url,
                raw_data=raw.raw_data,
                discovered_at=raw.discovered_at,
                expires_at=raw.expires_at,
            )
            try:
                session.add(db_job)
                session.flush()
                key_to_id[key] = raw.id
            except Exception:  # noqa: BLE001 - IntegrityError race; re-query
                session.rollback()
                row = (
                    session.query(Job.id)
                    .filter(Job.source == raw.source, Job.source_id == raw.source_id)
                    .first()
                )
                if row:
                    raw.id = row.id
                    key_to_id[key] = row.id

    session.commit()


def get_recent_jobs(
    session: Session,
    source: str | None = None,
    limit: int = 100,
) -> list[Job]:
    """Get recently discovered jobs, optionally filtered by source."""
    query = session.query(Job).order_by(Job.discovered_at.desc())
    if source:
        query = query.filter(Job.source == source)
    return query.limit(limit).all()


def mark_expired(session: Session, job_id: str) -> None:
    """Mark a job as expired (no longer accepting applications)."""
    session.query(Job).filter(Job.id == job_id).update({"expires_at": datetime.now(UTC)})
    session.commit()
