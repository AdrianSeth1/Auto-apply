"""Tracking and dashboard use cases shared by CLI and Web."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import ProgrammingError

from src.core.config import load_config

logger = logging.getLogger("autoapply.application.tracking")

VALID_OUTCOMES = {"pending", "rejected", "oa", "interview", "offer"}


def load_dashboard_data() -> dict:
    try:
        from src.core.database import get_session_factory
        from src.tracker.analytics import (
            compute_company_stats,
            compute_outcome_stats,
            compute_pipeline_stats,
        )
        from src.tracker.database import get_application_counts

        session_factory = get_session_factory(load_config())
        with session_factory() as session:
            pipeline_summary = compute_pipeline_stats(session)
            pipeline = get_application_counts(session)
            outcome_summary = compute_outcome_stats(session)
            companies = compute_company_stats(session)

        return {
            "pipeline": pipeline,
            "summary": _serialize_pipeline_summary(pipeline_summary),
            "outcomes": _serialize_outcome_stats(outcome_summary),
            "companies": [_serialize_company_stats(company) for company in companies],
            "db_connected": True,
            "error": None,
        }
    except Exception as exc:
        return {
            "pipeline": {},
            "summary": _empty_pipeline_summary(),
            "outcomes": _empty_outcome_stats(),
            "companies": [],
            "db_connected": False,
            "error": str(exc),
        }


def load_applications_data(
    *,
    status: str = "",
    outcome: str = "",
    company: str = "",
    limit: int = 50,
) -> dict:
    applications = []
    pipeline_stats = {}
    outcome_stats = _empty_outcome_stats()
    error = None

    try:
        from src.core.database import get_session_factory
        from src.tracker.database import get_applications_with_jobs

        session_factory = get_session_factory(load_config())
        with session_factory() as session:
            filtered_applications = get_applications_with_jobs(
                session,
                status=status or None,
                outcome=outcome or None,
                company=company or None,
                limit=None,
            )
            applications = filtered_applications[:limit]
            summaries = _summarize_applications(filtered_applications)
            pipeline_stats = summaries["pipeline_counts"]
            outcome_stats = summaries["outcomes"]
    except Exception as exc:
        error = str(exc)

    return {
        "applications": [_serialize_application(app, job) for app, job in applications],
        "pipeline": pipeline_stats,
        "outcomes": outcome_stats,
        "error": error,
        "filters": {"status": status, "outcome": outcome, "company": company, "limit": limit},
    }


def update_application_outcome(*, application_id: UUID, outcome: str) -> dict:
    if outcome not in VALID_OUTCOMES:
        return {
            "ok": False,
            "error": "Invalid outcome",
            "error_code": "invalid_outcome",
        }

    try:
        from src.core.database import get_session_factory
        from src.tracker.database import update_outcome

        session_factory = get_session_factory(load_config())
        with session_factory() as session:
            app = update_outcome(session, application_id, outcome)
            if outcome in {"oa", "interview", "offer"}:
                from src.application.funnel import record_event

                stage = "screen" if outcome == "oa" else outcome
                record_event(
                    session,
                    entity_type="application",
                    entity_id=app.id,
                    stage=stage,
                    job_id=app.job_id,
                    application_id=app.id,
                    evaluation_id=app.evaluation_id,
                    journey_key=str(app.evaluation_id) if app.evaluation_id else None,
                    profile_variant=app.profile_variant,
                    material_variant=app.material_variant,
                    time_spent_seconds=app.time_spent_seconds,
                    metadata={"outcome": outcome},
                    occurred_at=app.outcome_updated_at,
                    tenant_id=app.tenant_id,
                )
            session.commit()
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_code": "application_not_found",
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Failed to update outcome: {exc}",
            "error_code": "update_failed",
        }

    return {
        "ok": True,
        "status": "updated",
        "message": f"Updated to {outcome}",
        "application_id": str(app.id),
        "outcome": app.outcome,
        "updated_at": _isoformat(app.outcome_updated_at),
    }


def submit_paused_application(*, application_id: UUID) -> dict:
    """Queue submit for a paused application without marking it submitted.

    Phase 18 made ``application.submit`` honest: after the freshness gate
    clears, the worker still returns ``not_implemented`` for the external ATS
    click-submit step. This legacy endpoint must therefore not transition the
    application to ``SUBMITTED`` just because the operator approved the attempt.
    """
    try:
        from src.core.database import get_session_factory
        from src.core.state_machine import AppStatus
        from src.tasks.app import celery_app

        session_factory = get_session_factory(load_config())
        submit_task_id = None
        with session_factory() as session:
            from src.core.models import Application

            app = session.get(Application, application_id)
            if app is None:
                return {
                    "ok": False,
                    "error": "Application not found",
                    "error_code": "application_not_found",
                }
            if app.status != AppStatus.REVIEW_REQUIRED:
                return {
                    "ok": False,
                    "error": (
                        f"Application status is {app.status}; only paused applications "
                        "can be submitted."
                    ),
                    "error_code": "invalid_status",
                }

            try:
                async_result = celery_app.send_task(
                    "application.submit",
                    kwargs={"application_id": str(app.id)},
                )
                submit_task_id = str(async_result.id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to enqueue application.submit: %s", exc, exc_info=True)
                return {
                    "ok": False,
                    "error": f"Failed to enqueue submit task: {exc}",
                    "error_code": "submit_enqueue_failed",
                }

            now = datetime.now(UTC)
            history = list(app.state_history or [])
            history.append(
                {
                    "timestamp": now.isoformat(),
                    "event": "USER_APPROVED_SUBMIT_QUEUED",
                    "from": str(AppStatus.REVIEW_REQUIRED),
                    "to": str(AppStatus.REVIEW_REQUIRED),
                    "meta": {
                        "submit_task_id": submit_task_id,
                        "note": (
                            "Application is not marked SUBMITTED until a real submit worker "
                            "confirms external ATS submission."
                        ),
                    },
                }
            )
            app.state_history = history
            session.commit()

            return {
                "ok": True,
                "status": "submit_queued",
                "message": (
                    "Submit task queued; application is not marked submitted until external "
                    "ATS submission is implemented and confirmed."
                ),
                "application_id": str(app.id),
                "submit_task_id": submit_task_id,
                "submitted_at": _isoformat(app.submitted_at),
            }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Failed to submit application: {exc}",
            "error_code": "submit_failed",
        }


def mark_application_submitted_manually(*, application_id: UUID) -> dict:
    """User confirms they submitted this application by hand on the ATS.

    2026-07-07: Phase 18 stopped legacy paths from auto-marking rows
    SUBMITTED because the external click-submit worker doesn't exist —
    correct for automation, but it left no way to record a submission
    the USER personally performed. Without ``submitted_at``, manual
    applies were invisible to email ingestion, follow-up nudges, and
    outcome analytics. A user-confirmed manual submission is the
    strongest confirmation there is.

    Also flips any pending/approved review-queue entry for the same job
    to ``submitted`` so the review pile clears.
    """
    try:
        from src.application.review import approve as approve_entry
        from src.application.review import mark_submitted as mark_entry_submitted
        from src.core.database import get_session_factory
        from src.core.models import ReviewQueueEntry
        from src.core.state_machine import AppStatus

        session_factory = get_session_factory(load_config())
        with session_factory() as session:
            from src.core.models import Application

            app = session.get(Application, application_id)
            if app is None:
                return {
                    "ok": False,
                    "error": "Application not found",
                    "error_code": "application_not_found",
                }
            if app.status == str(AppStatus.SUBMITTED):
                return {
                    "ok": True,
                    "status": "already_submitted",
                    "message": "Already marked submitted.",
                    "application_id": str(app.id),
                    "submitted_at": _isoformat(app.submitted_at),
                }

            now = datetime.now(UTC)
            history = list(app.state_history or [])
            history.append(
                {
                    "timestamp": now.isoformat(),
                    "event": "USER_CONFIRMED_MANUAL_SUBMISSION",
                    "from": str(app.status),
                    "to": str(AppStatus.SUBMITTED),
                    "meta": {
                        "note": "User confirmed they submitted on the external ATS by hand."
                    },
                }
            )
            app.state_history = history
            app.status = str(AppStatus.SUBMITTED)
            app.submitted_at = now
            if app.created_at:
                app.time_spent_seconds = max(0, int((now - app.created_at).total_seconds()))

            # Clear matching review entries so the pile empties too.
            entries = (
                session.query(ReviewQueueEntry)
                .filter(
                    ReviewQueueEntry.job_id == app.job_id,
                    ReviewQueueEntry.status.in_(("pending", "approved")),
                )
                .all()
            )
            for entry in entries:
                try:
                    if entry.status == "pending":
                        approve_entry(
                            session, entry.id, reviewer="operator", reason="manual submission"
                        )
                    mark_entry_submitted(
                        session,
                        entry.id,
                        reviewer="operator",
                        reason="user submitted manually on the ATS",
                    )
                except Exception:  # noqa: BLE001 -- entry cleanup is best-effort
                    logger.debug("review entry cleanup skipped", exc_info=True)

            from src.application.funnel import record_event

            record_event(
                session,
                entity_type="application",
                entity_id=app.id,
                stage="applied",
                job_id=app.job_id,
                application_id=app.id,
                evaluation_id=app.evaluation_id,
                journey_key=str(app.evaluation_id) if app.evaluation_id else None,
                profile_variant=app.profile_variant,
                material_variant=app.material_variant,
                time_spent_seconds=app.time_spent_seconds,
                metadata={"submission_method": "manual"},
                occurred_at=now,
                tenant_id=app.tenant_id,
            )

            session.commit()
            return {
                "ok": True,
                "status": "submitted",
                "message": "Marked as submitted — it's now in outcome tracking.",
                "application_id": str(app.id),
                "submitted_at": _isoformat(app.submitted_at),
                "review_entries_cleared": len(entries),
            }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Failed to mark application submitted: {exc}",
            "error_code": "mark_submitted_failed",
        }


def discard_paused_application(
    *,
    application_id: UUID,
    reason: str | None = None,
) -> dict:
    """Abandon a paused application: REVIEW_REQUIRED → FAILED.

    The state machine treats FAILED as a terminal error state that any
    active state can reach, so this is a valid transition. We record the
    user-supplied reason (if any) in ``state_history`` so the discard
    intent isn't conflated with a system failure.
    """
    try:
        from src.core.database import get_session_factory
        from src.core.state_machine import AppStatus

        session_factory = get_session_factory(load_config())
        with session_factory() as session:
            from src.core.models import Application

            app = session.get(Application, application_id)
            if app is None:
                return {
                    "ok": False,
                    "error": "Application not found",
                    "error_code": "application_not_found",
                }
            if app.status != AppStatus.REVIEW_REQUIRED:
                return {
                    "ok": False,
                    "error": (
                        f"Application status is {app.status}; only paused "
                        "applications can be discarded."
                    ),
                    "error_code": "invalid_status",
                }

            now = datetime.now(UTC)
            app.status = str(AppStatus.FAILED)
            history = list(app.state_history or [])
            history.append(
                {
                    "timestamp": now.isoformat(),
                    "event": "USER_DISCARDED",
                    "from": str(AppStatus.REVIEW_REQUIRED),
                    "to": str(AppStatus.FAILED),
                    "meta": {
                        "reason": (reason or "").strip() or None,
                        "discarded_by": "user",
                    },
                }
            )
            app.state_history = history
            session.commit()

            return {
                "ok": True,
                "status": "discarded",
                "message": "Application discarded.",
                "application_id": str(app.id),
            }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Failed to discard application: {exc}",
            "error_code": "discard_failed",
        }


def soft_delete_application(
    *,
    application_id: UUID,
    cascade: bool = False,
    reason: str | None = None,
) -> dict:
    """Phase 18.4: soft-delete an application.

    Sets ``Application.deleted_at`` to the current timestamp. When
    ``cascade=True``, the resume / cover-letter artifact paths the
    row references are moved into the artifact-cleanup quarantine
    immediately (instead of waiting for the
    ``cleanup.soft_deleted_retention_days`` window). The row itself
    stays around until that window expires so the audit trail is
    preserved.

    Returns the standard ``{ok, ...}`` envelope used by the
    surrounding routes. ``cascade`` failures degrade gracefully: the
    soft-delete succeeds even if the cascade move can't find a file.
    """
    try:
        from src.core.database import get_session_factory
        from src.core.models import Application

        session_factory = get_session_factory(load_config())
        with session_factory() as session:
            app = session.get(Application, application_id)
            if app is None:
                return {
                    "ok": False,
                    "error": "Application not found",
                    "error_code": "application_not_found",
                }
            if app.deleted_at is not None:
                return {
                    "ok": True,
                    "status": "already_deleted",
                    "message": "Application was already soft-deleted.",
                    "application_id": str(app.id),
                    "deleted_at": app.deleted_at.isoformat(),
                    "cascade": False,
                }
            now = datetime.now(UTC)
            app.deleted_at = now
            history = list(app.state_history or [])
            history.append(
                {
                    "timestamp": now.isoformat(),
                    "event": "USER_DELETED",
                    "from": str(app.status),
                    "to": str(app.status),
                    "meta": {
                        "reason": (reason or "").strip() or None,
                        "cascade": bool(cascade),
                    },
                }
            )
            app.state_history = history

            cascade_paths: list[str] = []
            if cascade:
                for raw in (app.resume_version, app.cover_letter_version):
                    if isinstance(raw, str) and raw:
                        cascade_paths.append(raw)
            session.commit()

        cascade_result: dict | None = None
        if cascade:
            cascade_result = _cascade_quarantine_application_artifacts(
                tenant_id=str(app.tenant_id),
                paths=cascade_paths,
            )

        return {
            "ok": True,
            "status": "deleted",
            "message": "Application soft-deleted.",
            "application_id": str(app.id),
            "deleted_at": now.isoformat(),
            "cascade": bool(cascade),
            "cascade_summary": cascade_result,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"Failed to soft-delete application: {exc}",
            "error_code": "soft_delete_failed",
        }


def _cascade_quarantine_application_artifacts(
    *, tenant_id: str, paths: list[str]
) -> dict:
    """Helper for cascade=True: run a one-shot ``clean`` over just the
    referenced files. We piggyback on the Phase 18.4 cleanup pipeline
    rather than reinvent the quarantine layout."""
    if not paths:
        return {"moved": 0, "missing": 0}
    import shutil
    from pathlib import Path
    from uuid import uuid4

    from src.core.database import get_session_factory
    from src.core.models import CleanupItem, CleanupRun
    from src.maintenance.artifacts import (
        ACTION_QUARANTINED,
        CATEGORY_ORPHAN_OUTPUT,
        QUARANTINE_ROOT,
    )

    run_id = uuid4()
    run_dir = QUARANTINE_ROOT / run_id.hex
    moved = 0
    missing = 0
    session_factory = get_session_factory(load_config())
    now = datetime.now(UTC)
    with session_factory() as session, session.begin():
        run = CleanupRun(
            id=run_id,
            tenant_id=tenant_id,
            mode="clean",
            trigger="api",
            started_at=now,
        )
        session.add(run)
        # FK fix: ``CleanupItem.run_id`` has a foreign key to
        # ``cleanup_runs.id`` but no SQLAlchemy ``relationship`` declared,
        # so the unit-of-work has no dependency edge between the parent
        # and child rows. When both are added inside the same begin()
        # block, the flush order is not guaranteed -- with bulk INSERT
        # enabled, children can be issued before the parent, which trips
        # the ``fk_cleanup_items_run`` constraint and aborts the delete.
        # Flushing here forces the ``cleanup_runs`` INSERT to land first
        # so every subsequent ``CleanupItem`` has a parent row to point at.
        session.flush()
        for raw in paths:
            source = Path(raw)
            if not source.is_absolute():
                from src.core.config import PROJECT_ROOT

                source = PROJECT_ROOT / source
            if not source.exists():
                missing += 1
                continue
            destination = run_dir / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                size = source.stat().st_size
            except OSError:
                size = None
            try:
                shutil.move(str(source), str(destination))
            except OSError:
                missing += 1
                continue
            moved += 1
            session.add(
                CleanupItem(
                    run_id=run_id,
                    tenant_id=tenant_id,
                    path=str(source),
                    quarantine_path=str(destination),
                    category=CATEGORY_ORPHAN_OUTPUT,
                    action=ACTION_QUARANTINED,
                    size_bytes=size,
                    mtime=None,
                    quarantined_at=now,
                    reason="cascade=True on DELETE /api/applications",
                )
            )
        run.finished_at = datetime.now(UTC)
        run.scanned_count = moved + missing
        run.quarantined_count = moved
        run.summary = {
            "trigger": "api",
            "moved": moved,
            "missing": missing,
            "paths": paths,
        }
    return {"moved": moved, "missing": missing, "run_id": run_id.hex}


def load_status_data(
    *,
    company: str | None = None,
    app_status: str | None = None,
    outcome: str | None = None,
    limit: int = 20,
) -> dict:
    try:
        from src.core.database import get_session_factory

        session_factory = get_session_factory(load_config())
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_code": "database_connection_failed",
        }

    with session_factory() as session:
        from src.tracker.database import get_applications_with_jobs

        try:
            filtered_applications = get_applications_with_jobs(
                session,
                status=app_status,
                outcome=outcome,
                company=company,
                limit=None,
            )
            summaries = _summarize_applications(filtered_applications)
        except ProgrammingError as exc:
            logger.debug("Status analytics failed due to schema mismatch: %s", exc)
            return {
                "ok": False,
                "error": str(exc),
                "error_code": "schema_out_of_date",
            }

        recent = filtered_applications[:limit]

    return {
        "ok": True,
        "filters": {
            "company": company,
            "status": app_status,
            "outcome": outcome,
            "limit": limit,
        },
        "pipeline_counts": summaries["pipeline_counts"],
        "pipeline_summary": summaries["pipeline_summary"],
        "outcomes": summaries["outcomes"],
        "companies": summaries["companies"],
        "platforms": summaries["platforms"],
        "recent_applications": [_serialize_application(app, job) for app, job in recent],
    }


def export_applications_csv_data(*, output_path: Path) -> dict:
    try:
        from src.core.database import get_session_factory
        from src.tracker.export import export_applications_csv

        session_factory = get_session_factory(load_config())
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_code": "database_connection_failed",
        }

    with session_factory() as session:
        csv_content = export_applications_csv(session, output_path=output_path)

    row_count = max(len(csv_content.splitlines()) - 1, 0)
    return {
        "ok": True,
        "exported_to": str(output_path),
        "row_count": row_count,
    }


def _serialize_pipeline_summary(summary) -> dict:
    return {
        "total_discovered": summary.total_discovered,
        "total_applied": summary.total_applied,
        "total_failed": summary.total_failed,
        "total_review": summary.total_review,
        "avg_match_score": summary.avg_match_score,
        "avg_fields_filled_pct": summary.avg_fields_filled_pct,
    }


def _serialize_outcome_stats(summary) -> dict:
    return {
        "total": summary.total_submitted,
        "pending": summary.pending,
        "rejected": summary.rejected,
        "oa": summary.oa,
        "interview": summary.interview,
        "offer": summary.offer,
        "rates": {
            "response_rate": summary.response_rate,
            "positive_rate": summary.positive_rate,
        },
    }


def _serialize_company_stats(company) -> dict:
    return {
        "company": company.company,
        "applications": company.applications,
        "submitted": company.submitted,
        "outcomes": company.outcomes,
        "avg_match_score": company.avg_match_score,
    }


def _serialize_application(app, job) -> dict:
    return {
        "id": str(app.id),
        "job_id": str(app.job_id),
        "status": app.status,
        "match_score": app.match_score,
        "outcome": app.outcome or "pending",
        "created_at": _isoformat(app.created_at),
        "updated_at": _isoformat(app.updated_at),
        "submitted_at": _isoformat(app.submitted_at),
        "fields_filled": app.fields_filled,
        "fields_total": app.fields_total,
        "fill_details": getattr(app, "fill_details", None) or [],
        "files_uploaded": getattr(app, "files_uploaded", None) or [],
        "qa_responses": getattr(app, "qa_responses", None) or {},
        "screenshot_paths": getattr(app, "screenshot_paths", None) or [],
        "resume_version": getattr(app, "resume_version", None),
        "cover_letter_version": getattr(app, "cover_letter_version", None),
        "error_log": getattr(app, "error_log", None),
        "job": {
            "id": str(job.id),
            "company": job.company,
            "title": job.title,
            "location": job.location,
            "application_url": job.application_url,
            "ats_type": job.ats_type,
        },
    }


def _summarize_applications(records: list[tuple]) -> dict:
    pipeline_counts: dict[str, int] = {}
    company_map: dict[str, list[tuple]] = {}
    platform_map: dict[str, dict[str, int]] = {}

    match_scores = []
    fill_rates = []
    total_applied = 0
    total_failed = 0
    total_review = 0
    outcome_counts = {"pending": 0, "rejected": 0, "oa": 0, "interview": 0, "offer": 0}

    for app, job in records:
        pipeline_counts[app.status] = pipeline_counts.get(app.status, 0) + 1
        company_map.setdefault(job.company, []).append((app, job))

        ats_type = job.ats_type or "unknown"
        platform_counts = platform_map.setdefault(ats_type, {})
        platform_counts[app.status] = platform_counts.get(app.status, 0) + 1

        if app.status == "SUBMITTED":
            total_applied += 1
            normalized_outcome = app.outcome or "pending"
            outcome_counts[normalized_outcome] = outcome_counts.get(normalized_outcome, 0) + 1
        elif app.status == "FAILED":
            total_failed += 1
        elif app.status == "REVIEW_REQUIRED":
            total_review += 1

        if app.match_score is not None:
            match_scores.append(app.match_score)

        if app.fields_total:
            fill_rates.append((app.fields_filled or 0) / app.fields_total)

    companies = []
    for company_name, items in company_map.items():
        submitted = sum(1 for app, _ in items if app.status == "SUBMITTED")
        scores = [app.match_score for app, _ in items if app.match_score is not None]
        company_outcomes = {}
        for app, _ in items:
            normalized_outcome = app.outcome or "pending"
            company_outcomes[normalized_outcome] = company_outcomes.get(normalized_outcome, 0) + 1

        companies.append(
            {
                "company": company_name,
                "applications": len(items),
                "submitted": submitted,
                "outcomes": company_outcomes,
                "avg_match_score": sum(scores) / len(scores) if scores else 0.0,
            }
        )

    companies.sort(key=lambda item: item["applications"], reverse=True)
    total_submitted = (
        outcome_counts["pending"]
        + outcome_counts["rejected"]
        + outcome_counts["oa"]
        + outcome_counts["interview"]
        + outcome_counts["offer"]
    )

    return {
        "pipeline_counts": pipeline_counts,
        "pipeline_summary": {
            "total_discovered": len(records),
            "total_applied": total_applied,
            "total_failed": total_failed,
            "total_review": total_review,
            "avg_match_score": sum(match_scores) / len(match_scores) if match_scores else 0.0,
            "avg_fields_filled_pct": sum(fill_rates) / len(fill_rates) if fill_rates else 0.0,
        },
        "outcomes": {
            "total": total_submitted,
            "pending": outcome_counts["pending"],
            "rejected": outcome_counts["rejected"],
            "oa": outcome_counts["oa"],
            "interview": outcome_counts["interview"],
            "offer": outcome_counts["offer"],
            "rates": {
                "response_rate": (
                    (total_submitted - outcome_counts["pending"]) / total_submitted
                    if total_submitted
                    else 0.0
                ),
                "positive_rate": (
                    (outcome_counts["oa"] + outcome_counts["interview"] + outcome_counts["offer"])
                    / total_submitted
                    if total_submitted
                    else 0.0
                ),
            },
        },
        "companies": companies,
        "platforms": platform_map,
    }


def _empty_pipeline_summary() -> dict:
    return {
        "total_discovered": 0,
        "total_applied": 0,
        "total_failed": 0,
        "total_review": 0,
        "avg_match_score": 0.0,
        "avg_fields_filled_pct": 0.0,
    }


def _empty_outcome_stats() -> dict:
    return {
        "total": 0,
        "pending": 0,
        "rejected": 0,
        "oa": 0,
        "interview": 0,
        "offer": 0,
        "rates": {"response_rate": 0.0, "positive_rate": 0.0},
    }


def _isoformat(value) -> str | None:
    return value.isoformat() if value is not None else None
