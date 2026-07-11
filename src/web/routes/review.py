"""Phase 17.3 ``/api/review`` routes.

Thin wrappers over :mod:`src.application.review`. The frontend
``/review`` kanban (Phase 17.3 + 17.4) consumes:

* ``GET /api/review`` -- list entries; optional ``?status=`` filter.
* ``GET /api/review/{entry_id}`` -- single entry detail (popover).
* ``POST /api/review/{entry_id}/approve`` -- ``pending → approved``.
* ``POST /api/review/{entry_id}/reject`` -- ``pending|approved → rejected``.
* ``POST /api/review/{entry_id}/refresh`` -- ``stale → pending`` (Phase
  17.5 staleness recovery; the UI button is "Refresh materials").
* ``POST /api/review/bulk/approve`` (Phase 17.4) -- multi-id approve.
* ``POST /api/review/bulk/reject`` (Phase 17.4) -- multi-id or
  by-filter reject.

Submission still runs through the Phase 17.5 pre-submit gate, but this
route does not mark entries ``submitted`` until a real external ATS
click-submit worker exists. Trying to PATCH straight to ``submitted``
returns a 409 with the gate-required reason.

Auth: routes resolve ``tenant_id`` from the request (Phase 18 wires
this to the session; today the helper falls back to ``"default"``).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.application.review import (
    approve as approve_entry,
)
from src.application.review import (
    bulk_approve,
    bulk_reject,
    bulk_reject_by_filter,
    list_entries,
    refresh_stale,
    serialize_entry,
)
from src.application.review import (
    get_entry as get_entry_db,
)
from src.application.review import (
    reject as reject_entry,
)
from src.core.database import get_session_factory
from src.review.state_machine import InvalidTransitionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review", tags=["review"])


def _tenant() -> str:
    """Resolve the active tenant id.

    Phase 14 set up a ContextVar; Phase 18 will wire it to the session.
    Today this falls back to ``"default"`` so single-tenant deployments
    keep working without auth.
    """
    try:
        from src.tasks.context import current_tenant_id  # noqa: PLC0415

        tid = current_tenant_id()
    except Exception:  # noqa: BLE001
        tid = None
    return tid or "default"


# --------------------------------------------------------------------------- #
# Payload models                                                              #
# --------------------------------------------------------------------------- #


class ReviewActionPayload(BaseModel):
    """Body for single-item approve/reject/refresh routes."""

    reason: str | None = None
    reviewer: str | None = None


class BulkActionPayload(BaseModel):
    """Body for ``POST /api/review/bulk/approve|reject`` with explicit ids."""

    entry_ids: list[str] = Field(default_factory=list)
    reason: str | None = None
    reviewer: str | None = None


class BulkRejectByFilterPayload(BaseModel):
    """Body for ``POST /api/review/bulk/reject-by-filter``.

    Either ``company`` or ``keyword_in_title`` must be set (the route
    returns 400 if neither is). Both can be combined; matches are
    ``AND``-ed.
    """

    company: str | None = None
    keyword_in_title: str | None = None
    reason: str | None = None
    reviewer: str | None = None


# --------------------------------------------------------------------------- #
# Read routes                                                                 #
# --------------------------------------------------------------------------- #


def _entry_artifacts(materials_path: str | None) -> list[dict[str, str]]:
    """Derive downloadable artifact links from the entry's materials path.

    2026-07-08: plan-run entries store one path (usually the resume);
    the cover letter and alternate formats live next to it following the
    ``{type}_{company}_{role}_{date}`` naming pattern. Probe the
    resume_/cover_letter_ prefix swaps and .pdf/.docx variants and
    return only files that exist, so the kanban card can link everything
    the worker actually produced.
    """
    from pathlib import Path  # noqa: PLC0415

    if not materials_path:
        return []
    base = Path(materials_path)
    stems = {base.stem}
    # The naming pattern's {type} is "resume" / "cover" (with legacy
    # "cover_letter" seen in older artifacts) — probe all spellings.
    for resume_prefix, cover_prefix in (
        ("resume_", "cover_"),
        ("resume_", "cover_letter_"),
    ):
        if base.stem.startswith(resume_prefix):
            stems.add(cover_prefix + base.stem[len(resume_prefix):])
        elif base.stem.startswith(cover_prefix):
            stems.add(resume_prefix + base.stem[len(cover_prefix):])

    artifacts: list[dict[str, str]] = []
    seen: set[str] = set()
    for stem in sorted(stems):
        for ext in (".pdf", ".docx"):
            candidate = base.with_name(stem + ext)
            key = str(candidate)
            if key in seen or not candidate.exists():
                continue
            seen.add(key)
            kind = "Cover letter" if stem.startswith("cover") else "Resume"
            artifacts.append(
                {"label": f"{kind} {ext[1:].upper()}", "path": key}
            )
    return artifacts


def _entry_application_url(session, entry) -> str | None:
    """Posting URL for the kanban card: legacy Job first, then Job Index."""
    from src.core.models import Job, JobPosting  # noqa: PLC0415

    if entry.job_id is None:
        return None
    job = session.get(Job, entry.job_id)
    if job is not None and job.application_url:
        return job.application_url
    posting = session.get(JobPosting, entry.job_id)
    if posting is not None:
        return posting.canonical_url
    return None


def _entry_job_text(session, entry) -> tuple[str, str]:
    """(title, description) for QA-bank matching -- legacy Job first, then
    the Job Index snapshot bound to this entry."""
    from src.core.models import Job, JobSnapshot  # noqa: PLC0415

    if entry.job_id is not None:
        job = session.get(Job, entry.job_id)
        if job is not None and (job.description or job.title):
            return job.title or entry.title or "", job.description or ""
    if entry.job_snapshot_id is not None:
        snapshot = session.get(JobSnapshot, entry.job_snapshot_id)
        if snapshot is not None:
            return snapshot.title or entry.title or "", snapshot.description or ""
    return entry.title or "", ""


def _qa_bank_matches_for_job(title: str, description: str, limit: int = 5) -> list[dict]:
    """Top saved QA-bank entries whose saved question overlaps this job's
    title/description.

    Same token-overlap technique as
    ``src.application.question_answers._similar_saved_answers``, matched
    against the JOB's text instead of a live user-typed question -- the
    copy pack has no question in hand, just the posting.
    """
    import re as _re  # noqa: PLC0415

    from src.application.question_answers import list_saved_answers  # noqa: PLC0415

    saved = list_saved_answers().get("entries", [])
    if not saved:
        return []
    job_tokens = set(_re.findall(r"[a-z0-9]+", f"{title} {description}".lower()))
    if not job_tokens:
        return []
    scored = []
    for entry in saved:
        text = (entry.get("question") or "").lower()
        overlap = len(job_tokens & set(_re.findall(r"[a-z0-9]+", text)))
        if overlap >= 3:
            scored.append((overlap, entry))
    scored.sort(key=lambda pair: -pair[0])
    return [entry for _, entry in scored[:limit]]


def _active_profile_identity() -> dict[str, str]:
    """Identity fields for the copy pack: name, email, phone, location, LinkedIn."""
    from src.application.profile import get_active_profile_path  # noqa: PLC0415
    from src.memory.profile import load_profile_yaml  # noqa: PLC0415

    path = get_active_profile_path()
    if path is None:
        return {}
    try:
        profile = load_profile_yaml(path)
    except Exception:  # noqa: BLE001 -- best-effort, never break the copy pack
        logger.warning("copy-pack: failed to load active profile %s", path, exc_info=True)
        return {}
    identity = profile.get("identity") or {}
    return {
        "full_name": identity.get("full_name") or "",
        "email": identity.get("email") or "",
        "phone": identity.get("phone") or "",
        "location": identity.get("location") or "",
        "linkedin_url": identity.get("linkedin_url") or "",
    }


def _serialize_enriched(session, entry) -> dict[str, Any]:
    """serialize_entry + the fields a human needs to apply manually.

    The bare serializer left the operator dead-ended: no posting link,
    no artifact links — the review card showed a company and title and
    nothing actionable (user report, 2026-07-08).
    """
    payload = serialize_entry(entry)
    payload["application_url"] = _entry_application_url(session, entry)
    payload["artifacts"] = _entry_artifacts(entry.materials_path)
    return payload


@router.get("")
async def list_review_entries(
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """Read path for the kanban board.

    ``status=None`` returns every status for the current tenant; the
    UI groups them client-side. The implicit ordering is ``created_at
    DESC`` so the most recent plan run is on top.
    """
    factory = get_session_factory()
    with factory() as session:
        entries = list_entries(
            session, tenant_id=_tenant(), status=status, limit=limit  # type: ignore[arg-type]
        )
        return {
            "ok": True,
            "entries": [_serialize_enriched(session, e) for e in entries],
        }


@router.get("/{entry_id}")
async def get_review_entry(entry_id: str) -> dict[str, Any]:
    factory = get_session_factory()
    with factory() as session:
        entry = get_entry_db(session, entry_id)
        if entry is None or entry.tenant_id != _tenant():
            # Treat cross-tenant access as not-found so we don't leak
            # whether an id exists in another tenant's scope.
            raise HTTPException(404, "review entry not found")
        return {"ok": True, "entry": _serialize_enriched(session, entry)}


@router.get("/{entry_id}/copy-pack")
async def copy_pack_route(entry_id: str) -> dict[str, Any]:
    """Everything needed to fill out a manual ATS application by hand.

    2026-07-11 (user report): the user applies manually and re-typing
    identity fields, hunting for the right saved QA answer, and
    re-locating artifact file paths on every posting was slow. This
    bundles it all into one fetch so the "Copy pack" button can render a
    copy-everything modal on the review card.
    """
    factory = get_session_factory()
    with factory() as session:
        entry = get_entry_db(session, entry_id)
        if entry is None or entry.tenant_id != _tenant():
            raise HTTPException(404, "review entry not found")

        title, description = _entry_job_text(session, entry)
        return {
            "ok": True,
            "entry_id": str(entry.id),
            "company": entry.company,
            "title": entry.title,
            "identity": _active_profile_identity(),
            "artifacts": _entry_artifacts(entry.materials_path),
            "application_url": _entry_application_url(session, entry),
            "qa_matches": _qa_bank_matches_for_job(title, description),
        }


# --------------------------------------------------------------------------- #
# Single-item write routes                                                    #
# --------------------------------------------------------------------------- #


def _wrap_transition(callable_, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Common write-path error handling.

    Maps:
      * InvalidTransitionError -> 409
      * LookupError -> 404
      * SQLAlchemyError -> 500 with a non-leaky message

    A successful call returns ``{ok: True, entry: serialize_entry(...)}``.
    """
    try:
        entry = callable_(*args, **kwargs)
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(500, "database error") from exc
    return {"ok": True, "entry": serialize_entry(entry)}


# --------------------------------------------------------------------------- #
# Bulk routes (Phase 17.4)                                                    #
# --------------------------------------------------------------------------- #
#
# Declared BEFORE the single-item /{entry_id}/* routes so FastAPI's
# path matcher doesn't fall through ``/bulk/approve`` -> ``entry_id="bulk"``.


@router.post("/bulk/approve")
async def bulk_approve_route(payload: BulkActionPayload) -> dict[str, Any]:
    """Phase 17.4 -- multi-id approve.

    Returns aggregate ``{succeeded: [...], failed: [{id, error}]}`` so
    the UI can render ``"8 of 12 approved -- 4 failed: ..."`` in one
    pass. The route does NOT short-circuit on first failure -- this is
    a deliberate kanban affordance.
    """
    if not payload.entry_ids:
        raise HTTPException(400, "entry_ids is required")
    tenant = _tenant()
    factory = get_session_factory()
    with factory() as session, session.begin():
        # Tenant guard: filter to ids that belong to this tenant.
        owned = []
        for eid in payload.entry_ids:
            entry = get_entry_db(session, eid)
            if entry is not None and entry.tenant_id == tenant:
                owned.append(eid)
        result = bulk_approve(
            session,
            owned,
            reviewer=payload.reviewer,
            reason=payload.reason,
        )
    return {"ok": True, **result.to_dict()}


@router.post("/bulk/reject")
async def bulk_reject_route(payload: BulkActionPayload) -> dict[str, Any]:
    if not payload.entry_ids:
        raise HTTPException(400, "entry_ids is required")
    tenant = _tenant()
    factory = get_session_factory()
    with factory() as session, session.begin():
        owned = []
        for eid in payload.entry_ids:
            entry = get_entry_db(session, eid)
            if entry is not None and entry.tenant_id == tenant:
                owned.append(eid)
        result = bulk_reject(
            session,
            owned,
            reviewer=payload.reviewer,
            reason=payload.reason,
        )
    return {"ok": True, **result.to_dict()}


@router.post("/bulk/reject-by-filter")
async def bulk_reject_by_filter_route(
    payload: BulkRejectByFilterPayload,
) -> dict[str, Any]:
    if not payload.company and not payload.keyword_in_title:
        raise HTTPException(
            400, "either company or keyword_in_title is required"
        )
    factory = get_session_factory()
    with factory() as session, session.begin():
        result = bulk_reject_by_filter(
            session,
            tenant_id=_tenant(),
            company=payload.company,
            keyword_in_title=payload.keyword_in_title,
            reviewer=payload.reviewer,
            reason=payload.reason,
        )
    return {"ok": True, **result.to_dict()}


# --------------------------------------------------------------------------- #
# Single-item write routes (declared after /bulk/* so path matcher resolves   #
# /bulk/approve to the bulk route, not entry_id="bulk").                      #
# --------------------------------------------------------------------------- #


@router.post("/{entry_id}/approve")
async def approve_route(
    entry_id: str, payload: ReviewActionPayload
) -> dict[str, Any]:
    factory = get_session_factory()
    with factory() as session, session.begin():
        # Tenant isolation: load + check before transitioning.
        entry = get_entry_db(session, entry_id)
        if entry is None or entry.tenant_id != _tenant():
            raise HTTPException(404, "review entry not found")
        return _wrap_transition(
            approve_entry,
            session,
            entry_id,
            reviewer=payload.reviewer,
            reason=payload.reason,
        )


@router.post("/{entry_id}/reject")
async def reject_route(
    entry_id: str, payload: ReviewActionPayload
) -> dict[str, Any]:
    factory = get_session_factory()
    with factory() as session, session.begin():
        entry = get_entry_db(session, entry_id)
        if entry is None or entry.tenant_id != _tenant():
            raise HTTPException(404, "review entry not found")
        return _wrap_transition(
            reject_entry,
            session,
            entry_id,
            reviewer=payload.reviewer,
            reason=payload.reason,
        )


@router.post("/{entry_id}/refresh")
async def refresh_route(
    entry_id: str, payload: ReviewActionPayload
) -> dict[str, Any]:
    """``stale → pending`` + re-enqueue the upstream tasks that actually
    refresh the bound JD snapshot and materials.

    Codex P2 (round 3): without the task fan-out, the next approve+
    submit would re-evaluate the gate against unchanged data and
    bounce right back to ``stale`` -- there's no path forward. We now
    enqueue:

      * ``jobs.enrich`` -- re-scrape the JD into a fresh snapshot
        (Phase 13.4 already handles the snapshot creation + posting
        pointer update).
      * ``materials.generate`` -- regenerate the resume + cover letter
        against whatever snapshot ``jobs.enrich`` produces.

    Both task ids are surfaced so the kanban can render
    "Refresh queued: materials task X, scrape task Y" while the
    operator waits. The state machine transition still happens
    synchronously so the UI moves the card immediately.
    """
    factory = get_session_factory()
    with factory() as session, session.begin():
        entry = get_entry_db(session, entry_id)
        if entry is None or entry.tenant_id != _tenant():
            raise HTTPException(404, "review entry not found")

        try:
            refreshed = refresh_stale(
                session, entry_id, reviewer=payload.reviewer
            )
        except InvalidTransitionError as exc:
            raise HTTPException(409, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

        # Enqueue the upstream tasks. Broker hiccups land in the
        # response as null task ids; the operator can retry via a
        # second click. Logging the failure keeps the audit trail.
        enrich_task_id: str | None = None
        materials_task_id: str | None = None
        if entry.job_id is not None:
            try:
                from src.tasks.app import celery_app  # noqa: PLC0415

                enrich = celery_app.send_task(
                    "jobs.enrich",
                    kwargs={"posting_id": str(entry.job_id)},
                )
                enrich_task_id = str(enrich.id)
                materials = celery_app.send_task(
                    "materials.generate",
                    kwargs={
                        "job_id": str(entry.job_id),
                        "document_types": ["resume", "cover_letter"],
                    },
                )
                materials_task_id = str(materials.id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "review refresh_route: enqueue failed: %s",
                    exc,
                    exc_info=True,
                )

        return {
            "ok": True,
            "entry": serialize_entry(refreshed),
            "enrich_task_id": enrich_task_id,
            "materials_task_id": materials_task_id,
        }


@router.post("/{entry_id}/submit")
async def submit_route(
    entry_id: str, payload: ReviewActionPayload
) -> dict[str, Any]:
    """Phase 17.5: approve-and-submit via the pre-submit hard gate.

    Flow:
      1. Load the entry (tenant-scoped).
      2. Run the pre-submit gate (freshness + lifecycle). The gate
         may mutate the entry's status to ``stale`` or ``rejected``.
      3. If the gate cleared, enqueue ``application.submit`` only when
         a real ``Application`` row can be found. Do NOT flip the review
         entry to ``submitted`` here; Phase 18's worker does not perform
         the final external ATS click-submit yet.
      4. Return the structured gate verdict so the UI can render
         "Submit queued" / "Refresh required" / "Posting expired"
         deterministically.

    Returns 409 when the entry is not in ``approved`` state -- the
    caller must approve first.
    """
    from src.core.models import Application  # noqa: PLC0415
    from src.review.pre_submit_gate import run_pre_submit_gate  # noqa: PLC0415

    factory = get_session_factory()
    with factory() as session, session.begin():
        entry = get_entry_db(session, entry_id)
        if entry is None or entry.tenant_id != _tenant():
            raise HTTPException(404, "review entry not found")
        if entry.status != "approved":
            raise HTTPException(
                409,
                f"entry status is {entry.status!r}; approve first",
            )

        gate_result = run_pre_submit_gate(session, entry_id, auto_mutate=True)
        if not gate_result.allowed:
            # The gate has already mutated the entry to stale /
            # rejected as needed; surface the verdict.
            return {
                "ok": False,
                "gate": gate_result.to_dict(),
                "entry": serialize_entry(
                    get_entry_db(session, entry_id) or entry
                ),
            }

        app = None
        if entry.job_id is not None:
            app = session.execute(
                select(Application)
                .where(Application.tenant_id == entry.tenant_id)
                .where(Application.job_id == entry.job_id)
                .order_by(Application.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()

        # Enqueue the application.submit task. Lazy import keeps this
        # route fast in unit tests that don't have a Celery broker --
        # the import lives behind the gate so a missing Redis only
        # surfaces when we actually try to submit.
        submit_task_id = None
        if app is not None:
            try:
                from src.tasks.app import celery_app  # noqa: PLC0415

                async_result = celery_app.send_task(
                    "application.submit",
                    kwargs={"application_id": str(app.id)},
                )
                submit_task_id = str(async_result.id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "review submit_route: enqueue failed: %s",
                    exc,
                    exc_info=True,
                )

        return {
            "ok": False,
            "status": "submit_not_completed",
            "gate": gate_result.to_dict(),
            "entry": serialize_entry(
                get_entry_db(session, entry_id) or entry
            ),
            "application_id": str(app.id) if app is not None else None,
            "submit_task_id": submit_task_id,
            "detail": (
                "Pre-submit gate cleared, but Phase 18 does not perform the final external "
                "ATS click-submit. "
                "The review entry remains approved and must not be counted as submitted."
            ),
        }


@router.post("/{entry_id}/mark-submitted")
async def mark_submitted_manually_route(
    entry_id: str, payload: ReviewActionPayload
) -> dict[str, Any]:
    """2026-07-07: the user submitted this application BY HAND on the ATS.

    Phase 18 rightly refuses to auto-mark rows SUBMITTED because the
    external click-submit worker doesn't exist — but a manual submission
    the user personally performed IS a confirmed submission. Without
    this action the only way to clear a finished application from the
    review pile was Discard, which kept it out of outcome tracking
    entirely (email ingestion, follow-up nudges, and analytics all key
    off ``Application.submitted_at``).

    Transitions, atomically:
      * review entry: pending → approved → submitted (or approved →
        submitted); 409 for anything else.
      * matching Application (latest for the entry's job): status →
        SUBMITTED, ``submitted_at`` = now, state_history breadcrumb
        ``USER_CONFIRMED_MANUAL_SUBMISSION``.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    from src.application.review import approve as approve_entry  # noqa: PLC0415
    from src.application.review import mark_submitted as mark_entry_submitted  # noqa: PLC0415
    from src.core.models import Application  # noqa: PLC0415
    from src.core.state_machine import AppStatus  # noqa: PLC0415

    factory = get_session_factory()
    with factory() as session, session.begin():
        entry = get_entry_db(session, entry_id)
        if entry is None or entry.tenant_id != _tenant():
            raise HTTPException(404, "review entry not found")
        if entry.status not in ("pending", "approved"):
            raise HTTPException(
                409,
                f"entry status is {entry.status!r}; only pending/approved entries "
                "can be marked manually submitted",
            )

        if entry.status == "pending":
            approve_entry(
                session,
                entry.id,
                reviewer=payload.reviewer or "operator",
                reason=payload.reason or "manual submission",
            )
        entry = mark_entry_submitted(
            session,
            entry.id,
            reviewer=payload.reviewer or "operator",
            reason=payload.reason or "user submitted manually on the ATS",
        )

        app = None
        if entry.job_id is not None:
            app = session.execute(
                select(Application)
                .where(Application.tenant_id == entry.tenant_id)
                .where(Application.job_id == entry.job_id)
                .order_by(Application.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()

        now = datetime.now(UTC)
        if app is None and entry.job_id is not None:
            # 2026-07-08: plan-run review entries don't create Application
            # rows up front, so a manual submission had nothing to track
            # against — the user's application vanished from outcomes,
            # email matching, and follow-ups. Materialize the row here;
            # the manual submission is the moment it becomes real.
            from src.core.models import Job  # noqa: PLC0415

            legacy_job = session.get(Job, entry.job_id)
            if legacy_job is not None:
                score = None
                if isinstance(entry.score_breakdown, dict):
                    score = entry.score_breakdown.get("final_score")
                app = Application(
                    tenant_id=entry.tenant_id,
                    job_id=legacy_job.id,
                    job_snapshot_id=entry.job_snapshot_id,
                    status=str(AppStatus.SUBMITTED),
                    match_score=score,
                    resume_version=entry.materials_path,
                    submitted_at=now,
                    state_history=[
                        {
                            "timestamp": now.isoformat(),
                            "event": "USER_CONFIRMED_MANUAL_SUBMISSION",
                            "from": "NONE",
                            "to": str(AppStatus.SUBMITTED),
                            "meta": {
                                "review_entry_id": str(entry.id),
                                "note": (
                                    "Application row materialized from a plan-run review "
                                    "entry when the user confirmed a manual ATS submission."
                                ),
                            },
                        }
                    ],
                )
                session.add(app)
                session.flush()
        elif app is not None and app.status != str(AppStatus.SUBMITTED):
            history = list(app.state_history or [])
            history.append(
                {
                    "timestamp": now.isoformat(),
                    "event": "USER_CONFIRMED_MANUAL_SUBMISSION",
                    "from": str(app.status),
                    "to": str(AppStatus.SUBMITTED),
                    "meta": {
                        "review_entry_id": str(entry.id),
                        "note": "User confirmed they submitted on the external ATS by hand.",
                    },
                }
            )
            app.state_history = history
            app.status = str(AppStatus.SUBMITTED)
            app.submitted_at = now

        return {
            "ok": True,
            "status": "submitted",
            "entry": serialize_entry(entry),
            "application_id": str(app.id) if app is not None else None,
            "message": "Marked as submitted — it's now in outcome tracking.",
        }
