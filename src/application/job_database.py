"""Browse and act on jobs persisted locally.

The Jobs tab searches *live* sources; everything it finds is persisted to
one of TWO stores (see docs/INFRASTRUCTURE.md "The three job stores"):

* the legacy ``jobs`` table -- ATS batch results land here via
  ``persist_and_sync_ids``;
* the Job Index (``job_postings`` + ``job_snapshots``) -- LinkedIn
  searches land here via ``cached_search``.

This module is the read/act surface over BOTH, as a SQL ``UNION ALL`` so
filtering and pagination stay correct across stores:

* :func:`list_db_jobs` -- filter stored jobs by location (whole-word
  matching with US-state / country aliases, same semantics as the live
  search filters), employment type, seniority, source, company, and a
  free-text query over title+company.
* :func:`generate_materials_for_db_jobs` -- for selected job ids, create
  an Application record per job (materializing a legacy ``jobs`` row from
  the Job Index when needed) and enqueue ``materials.generate`` with
  ``application_id``. The worker attaches artifact paths and advances the
  application to ``REVIEW_REQUIRED``, so selected jobs land in the
  Awaiting Review "ready" section with apply link + material downloads --
  the surface used for applying manually.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import String, cast, exists, func, literal, select, union_all

from src.application.jobs import _matches_locations
from src.core.models import TENANT_DEFAULT, Application, Job, JobPosting, JobSnapshot

logger = logging.getLogger("autoapply.application.job_database")

# When a location filter is active we cannot express whole-word +
# alias matching in SQL, so we pull up to this many rows (newest
# first, after the SQL-expressible filters) and match in Python.
_LOCATION_SCAN_CAP = 30000

_MAX_BATCH_GENERATE = 50
_COMPANY_FACET_LIMIT = 40

_ALLOWED_DOCUMENT_TYPES = {"resume", "cover_letter"}


def _union_select():
    """Common-column UNION ALL over the legacy table and the Job Index.

    Index postings whose (source, source_id) already exist in the legacy
    table are excluded so a job discovered by both paths appears once.
    """
    legacy = select(
        cast(Job.id, String).label("id"),
        Job.source.label("source"),
        Job.source_id.label("source_id"),
        Job.company.label("company"),
        Job.title.label("title"),
        Job.location.label("location"),
        Job.employment_type.label("employment_type"),
        Job.seniority.label("seniority"),
        Job.ats_type.label("ats_type"),
        Job.application_url.label("application_url"),
        Job.description.is_not(None).label("has_description"),
        Job.discovered_at.label("discovered_at"),
        exists(
            select(literal(1)).where(
                Application.job_id == Job.id,
                Application.deleted_at.is_(None),
            )
        ).label("has_application"),
    ).where(Job.tenant_id == TENANT_DEFAULT)

    already_in_legacy = exists(
        select(literal(1)).where(
            Job.tenant_id == TENANT_DEFAULT,
            Job.source == JobPosting.source,
            Job.source_id == JobPosting.source_id,
        )
    )
    index = (
        select(
            cast(JobPosting.id, String).label("id"),
            JobPosting.source.label("source"),
            JobPosting.source_id.label("source_id"),
            JobPosting.company.label("company"),
            JobSnapshot.title.label("title"),
            JobSnapshot.location.label("location"),
            JobSnapshot.employment_type.label("employment_type"),
            JobSnapshot.seniority.label("seniority"),
            literal(None).label("ats_type"),
            func.coalesce(JobSnapshot.application_url, JobPosting.canonical_url).label(
                "application_url"
            ),
            JobSnapshot.description.is_not(None).label("has_description"),
            JobPosting.first_seen_at.label("discovered_at"),
            # Index postings gain a legacy twin (and drop out of this arm)
            # the first time materials are generated for them, so they can
            # never have an application here.
            literal(False).label("has_application"),
        )
        .join(JobSnapshot, JobPosting.latest_snapshot_id == JobSnapshot.id)
        .where(JobPosting.tenant_id == TENANT_DEFAULT, ~already_in_legacy)
    )
    return union_all(legacy, index).subquery("all_jobs")


def list_db_jobs(
    *,
    q: str = "",
    location: str = "",
    employment_type: str = "",
    seniority: str = "",
    source: str = "",
    company: str = "",
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Filter + paginate jobs stored in the local database (both stores).

    Returns ``{ok, jobs, total, limit, offset, facets}`` where ``facets``
    carries distinct values present in the DB so the UI can populate its
    filter dropdowns without hardcoding vocabulary.
    """
    from src.core.database import get_session_factory  # noqa: PLC0415

    limit = max(1, min(int(limit or 20), 200))
    offset = max(0, int(offset or 0))

    factory = get_session_factory()
    with factory() as session:
        jobs_sq = _union_select()

        stmt = select(jobs_sq)
        if employment_type.strip():
            stmt = stmt.where(jobs_sq.c.employment_type == employment_type.strip().lower())
        if seniority.strip():
            stmt = stmt.where(jobs_sq.c.seniority == seniority.strip().lower())
        if source.strip():
            stmt = stmt.where(jobs_sq.c.source == source.strip().lower())
        if company.strip():
            stmt = stmt.where(jobs_sq.c.company.ilike(f"%{company.strip()}%"))
        if q.strip():
            needle = f"%{q.strip()}%"
            stmt = stmt.where(jobs_sq.c.title.ilike(needle) | jobs_sq.c.company.ilike(needle))

        stmt = stmt.order_by(jobs_sq.c.discovered_at.desc())

        location_query = location.strip().lower()
        if location_query:
            # Whole-word/alias matching happens in Python; cap the scan.
            rows = session.execute(stmt.limit(_LOCATION_SCAN_CAP)).mappings().all()
            matched = [
                row for row in rows if _matches_locations(row["location"], [location_query])
            ]
            total = len(matched)
            page = matched[offset : offset + limit]
        else:
            total = session.execute(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            ).scalar_one()
            page = session.execute(stmt.limit(limit).offset(offset)).mappings().all()

        facets = {
            "employment_types": _distinct(session, jobs_sq, "employment_type"),
            "seniorities": _distinct(session, jobs_sq, "seniority"),
            "sources": _distinct(session, jobs_sq, "source"),
            "companies": _company_facet(session, jobs_sq),
        }

        return {
            "ok": True,
            "jobs": [_serialize_row(row) for row in page],
            "total": total,
            "limit": limit,
            "offset": offset,
            "facets": facets,
        }


def generate_materials_for_db_jobs(
    *,
    job_ids: list[str],
    document_types: list[str] | None = None,
) -> dict[str, Any]:
    """Queue resume / cover-letter generation for stored jobs.

    Per job id: resolve it to a legacy ``jobs`` row (creating one from
    the Job Index posting+snapshot when needed), create an
    :class:`Application` record, then enqueue ``materials.generate``
    with ``application_id`` so the worker writes the artifact paths onto
    the application AND flips it to ``REVIEW_REQUIRED``. Result: each
    selected job appears in the Awaiting Review "ready to apply" section
    with its application link and resume/cover downloads -- the surface
    used for manual applying.

    Jobs that already have a non-deleted application with materials are
    skipped (use "Replace materials" on the application card to
    regenerate), which also prevents accidental duplicate generation.

    Requires the Celery worker + Redis (same requirement as plan runs).
    Failures are reported per job; one bad id does not abort the batch.
    """
    from src.core.database import get_session_factory  # noqa: PLC0415
    from src.tasks.app import celery_app  # noqa: PLC0415

    document_types = [
        dt for dt in (document_types or ["resume", "cover_letter"])
        if dt in _ALLOWED_DOCUMENT_TYPES
    ] or ["resume", "cover_letter"]

    if not job_ids:
        return {"ok": False, "queued": [], "errors": ["No jobs selected."]}
    if len(job_ids) > _MAX_BATCH_GENERATE:
        return {
            "ok": False,
            "queued": [],
            "errors": [
                f"Too many jobs selected ({len(job_ids)}); max {_MAX_BATCH_GENERATE} per batch."
            ],
        }

    run_id = f"manual-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    queued: list[dict[str, Any]] = []
    errors: list[str] = []

    factory = get_session_factory()
    for raw_id in job_ids:
        try:
            job_uuid = UUID(str(raw_id))
        except (ValueError, TypeError):
            errors.append(f"Invalid job id: {raw_id}")
            continue

        try:
            with factory() as session, session.begin():
                job = _resolve_legacy_job(session, job_uuid)
                if job is None:
                    errors.append(f"Job not found: {raw_id}")
                    continue

                existing = (
                    session.execute(
                        select(Application).where(
                            Application.tenant_id == TENANT_DEFAULT,
                            Application.job_id == job.id,
                            Application.deleted_at.is_(None),
                        )
                    )
                    .scalars()
                    .first()
                )
                if existing is not None and (
                    existing.resume_version or existing.cover_letter_version
                ):
                    errors.append(
                        f"Already prepared: {job.company} - {job.title} "
                        "(regenerate from its card under Awaiting Review)"
                    )
                    continue

                application = existing
                if application is None:
                    application = Application(
                        tenant_id=TENANT_DEFAULT,
                        job_id=job.id,
                        status="QUALIFIED",
                        match_score=(job.raw_data or {}).get("match_score"),
                    )
                    session.add(application)
                    session.flush()

                job_id = str(job.id)
                application_id = str(application.id)
                company, title = job.company, job.title
        except Exception as exc:  # noqa: BLE001 -- per-job isolation
            errors.append(f"Preparing application failed for {raw_id}: {exc}")
            continue

        try:
            async_result = celery_app.send_task(
                "materials.generate",
                kwargs={
                    "job_id": job_id,
                    "application_id": application_id,
                    "document_types": document_types,
                },
            )
            queued.append(
                {
                    "job_id": job_id,
                    "company": company,
                    "title": title,
                    "task_id": str(async_result.id),
                    "application_id": application_id,
                }
            )
        except Exception as exc:  # noqa: BLE001 -- broker down etc.
            errors.append(f"Enqueue failed for {company} - {title}: {exc}")

    return {
        "ok": bool(queued) and not errors,
        "queued": queued,
        "errors": errors,
        "run_id": run_id,
        "document_types": document_types,
    }


def _resolve_legacy_job(session, job_uuid: UUID) -> Job | None:
    """Return the legacy ``jobs`` row for an id from either store.

    Ids from the Job Index arm of the union are ``JobPosting`` ids; the
    application/materials pipeline is keyed on legacy ``jobs`` rows
    (``applications.job_id`` is a FK), so we materialize a legacy row
    from the posting + latest snapshot on first use. Dedup key
    (source, source_id, lower(company)) is respected.
    """
    job = session.get(Job, job_uuid)
    if job is not None:
        return job

    posting = session.get(JobPosting, job_uuid)
    if posting is None:
        return None

    twin = (
        session.execute(
            select(Job).where(
                Job.tenant_id == TENANT_DEFAULT,
                Job.source == posting.source,
                Job.source_id == posting.source_id,
                func.lower(Job.company) == (posting.company or "").lower(),
            )
        )
        .scalars()
        .first()
    )
    if twin is not None:
        return twin

    snapshot = None
    if posting.latest_snapshot_id is not None:
        snapshot = session.get(JobSnapshot, posting.latest_snapshot_id)

    job = Job(
        tenant_id=TENANT_DEFAULT,
        source=posting.source,
        source_id=posting.source_id,
        company=posting.company,
        title=(snapshot.title if snapshot else "") or "",
        location=snapshot.location if snapshot else None,
        employment_type=snapshot.employment_type if snapshot else None,
        seniority=snapshot.seniority if snapshot else None,
        description=snapshot.description if snapshot else None,
        requirements=snapshot.requirements if snapshot else None,
        application_url=(
            (snapshot.application_url if snapshot else None) or posting.canonical_url
        ),
        raw_data=snapshot.raw_data if snapshot else None,
    )
    session.add(job)
    session.flush()
    return job


def _distinct(session, jobs_sq, column_name: str) -> list[str]:
    column = jobs_sq.c[column_name]
    rows = session.execute(
        select(column).where(column.is_not(None)).distinct().order_by(column)
    ).scalars()
    return [value for value in rows if value]


def _company_facet(session, jobs_sq) -> list[str]:
    rows = session.execute(
        select(jobs_sq.c.company, func.count().label("n"))
        .where(jobs_sq.c.company.is_not(None))
        .group_by(jobs_sq.c.company)
        .order_by(func.count().desc(), jobs_sq.c.company)
        .limit(_COMPANY_FACET_LIMIT)
    ).all()
    return [row[0] for row in rows if row[0]]


def _serialize_row(row) -> dict[str, Any]:
    discovered = row["discovered_at"]
    return {
        "id": str(row["id"]),
        "source": row["source"],
        "source_id": row["source_id"],
        "company": row["company"],
        "title": row["title"],
        "location": row["location"],
        "employment_type": row["employment_type"],
        "seniority": row["seniority"],
        "ats_type": row["ats_type"],
        "application_url": row["application_url"],
        "has_description": bool(row["has_description"]),
        "has_application": bool(row["has_application"]),
        "discovered_at": discovered.isoformat() if discovered else None,
    }
