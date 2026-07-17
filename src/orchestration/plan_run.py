"""Phase 17.1: ``plan_run`` orchestrator.

One invocation per scheduled Plan tick — could be hourly, daily, weekly,
or a manual "Run now". The name no longer implies a specific time of day.

* **Search** -- ``application.jobs.search_jobs`` with
  ``use_job_index=True``, which routes through Phase 13.4
  ``cached_search`` (cache-first, refresh stale via
  ``jobs.freshness.should_refresh(context="generate_materials")``).
* **Filter** -- ``matching.scorer.score_jobs`` with the active
  applicant profile; each ``ScoreBreakdown`` carries the Phase 16.1
  structured ``disqualify_results`` for the review-queue UI.
* **Top-N selection** -- qualified jobs ranked by ``final_score``, capped at
  ``top_n``, plus up to five startup bonus jobs that do not consume Top-N slots.
* **Enqueue** -- per top-N job: one ``materials.generate`` + one
  ``application.prepare`` task. Both ride the Phase 14 audit/trace
  trail; submission is never enqueued -- the operator approves via
  the Phase 17.3 review queue UI.

Boundaries
----------
* **Never auto-submits.** The orchestrator stops at
  ``application.prepare``. ``application.submit`` lands on the
  worker only after a human clicks "approve and submit" in the
  review queue, and even then the Phase 17.5 pre-submit hard gate
  re-runs ``should_refresh(..., "before_submit")``.
* **Per-tenant.** The Phase 14 ``tenant_id`` ContextVar must be set
  before ``run_plan`` is invoked (the Celery task wrapper handles
  this via ``AutoApplyTask.before_start``; CLI/test callers pass the
  tenant explicitly).
* **Pause-aware.** When the kill-switch sentinel
  (``data/plan_runs_paused``) exists, ``run_plan`` short-circuits
  with ``status="paused"`` so a scheduled tick doesn't generate cost
  on vacation.
* **Idempotent dry-run.** ``dry_run=True`` runs the search + filter
  but skips enqueue. Useful for the Phase 17.6 morning digest
  rehearsal and for CI.

Returned :class:`PlanRunReport` is JSON-serializable so the Phase 14
audit row can store it verbatim and the Phase 17.6 digest can read it
back without ORM access.
"""

from __future__ import annotations

import functools
import logging
import re
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_PLAN_SELECTION_LOCK = threading.Lock()
_MIN_SELECTION_SCORE = 0.50


# Where the Phase 17.7 kill switch lives. A tenant-aware version would
# nest by tenant; we keep one global sentinel for now since multi-
# tenancy hardening is Phase 18.
PLAN_RUN_PAUSE_SENTINEL_NAME = "plan_runs_paused"


@dataclass
class PlanRunReport:
    """Per-invocation summary persisted to the Phase 14 task audit row.

    All fields are intentionally JSON-serializable scalars / lists so
    the Phase 17.6 morning digest can read this back without a
    SQLAlchemy session.
    """

    run_id: str
    tenant_id: str
    profile_id: str
    search_profile_id: str | None
    status: str  # "ok" | "paused" | "no_profile" | "no_results" | "error"
    started_at: str  # ISO 8601 UTC
    finished_at: str
    duration_seconds: float
    top_n: int
    raw_jobs_fetched: int = 0
    search_filtered_out: int = 0
    source_counts: dict[str, int] = field(default_factory=dict)
    total_jobs_seen: int = 0
    qualified: int = 0
    disqualified: int = 0
    borderline: int = 0  # count of jobs whose final_score sits in [0.4, 0.6]
    selected: int = 0  # jobs that actually reached the enqueue step
    startup_bonus_selected: int = 0
    startup_selected_total: int = 0
    role_compatible: int = 0
    employer_quality_rejected: int = 0
    low_score_rejected: int = 0
    exact_duplicates_removed: int = 0
    previously_applied_removed: int = 0
    pending_deduplicated: int = 0
    new_review_entries: int = 0
    selected_jobs: list[dict[str, Any]] = field(default_factory=list)
    materials_task_ids: list[str] = field(default_factory=list)
    application_prepare_task_ids: list[str] = field(default_factory=list)
    application_submit_task_ids: list[str] = field(default_factory=list)
    review_entry_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    estimated_cost_usd: float = 0.0  # Phase 17.6 fills this with real telemetry
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Type aliases for the dependency-injected callables. Real callers wire
# these to ``application.jobs.search_jobs`` /
# ``matching.scorer.score_jobs`` / Celery's ``send_task``; tests inject
# stubs that don't touch Redis or the network.
SearchFn = Callable[..., Awaitable[dict[str, Any]]]
ScoreFn = Callable[[list[Any], Any], list[Any]]
EnqueueFn = Callable[[str, dict[str, Any]], str]


class PlanRunError(Exception):
    """Raised on programmer error (missing tenant, etc.). Worker
    failures are captured into :class:`PlanRunReport.errors` instead --
    a partial run should still produce a report so the digest has
    something to show."""


def plan_run_pause_sentinel_path(root: Path | None = None) -> Path:
    """The well-known sentinel path the kill switch (17.7) creates.

    Centralised here so the orchestrator + CLI + tests agree on it.
    """
    from src.core.config import PROJECT_ROOT  # local import; avoid cycle

    base = root if root is not None else PROJECT_ROOT
    return base / "data" / PLAN_RUN_PAUSE_SENTINEL_NAME


def plan_runs_paused(root: Path | None = None) -> bool:
    """Return True iff the sentinel exists.

    A symlink with no target counts as paused; that lets ops scripts
    park the sentinel however they like (touch / ln -s / mv).
    """
    return plan_run_pause_sentinel_path(root).exists()


def _now_utc() -> datetime:
    """Wall-clock now in UTC. Stubbed in tests via monkeypatch."""
    return datetime.now(UTC)


def _isoformat(dt: datetime) -> str:
    return dt.isoformat()


def _borderline_count(breakdowns: list[Any]) -> int:
    """Count qualified breakdowns whose final_score sits in [0.4, 0.6]
    (matches :data:`src.matching.edge_case_agent.BORDERLINE_LOW`).

    Imported lazily to keep this module light when the matching
    package isn't loaded (e.g. unit tests of the report shape).
    """
    from src.matching.edge_case_agent import BORDERLINE_HIGH, BORDERLINE_LOW

    return sum(
        1
        for b in breakdowns
        if not getattr(b, "disqualified", False)
        and BORDERLINE_LOW <= getattr(b, "final_score", 0.0) <= BORDERLINE_HIGH
    )


def _select_with_startup_bonus(
    qualified: list[Any], *, top_n: int, startup_target: int = 5
) -> tuple[list[Any], int]:
    """Select normal Top-N, then append startups until the target is met."""
    if top_n <= 0:
        return [], 0
    selected = list(qualified[:top_n])
    selected_ids = {str(getattr(item, "job_id", "")) for item in selected}
    startup_count = sum(bool(getattr(item, "is_startup", False)) for item in selected)
    appended = 0
    for item in qualified:
        if startup_count >= startup_target:
            break
        item_id = str(getattr(item, "job_id", ""))
        if item_id in selected_ids or not bool(getattr(item, "is_startup", False)):
            continue
        selected.append(item)
        selected_ids.add(item_id)
        startup_count += 1
        appended += 1
    return selected, appended


_ROLE_TITLE_PATTERNS = {
    "ai-solutions": re.compile(
        r"(?i)\b(forward[- ]?deployed|applied ai|ai (solutions?|implementation|"
        r"deployment|consultant|engineer)|genai|llm|customer engineer|"
        r"solutions architect|professional services engineer)\b"
    ),
    "implementation-consultant": re.compile(
        r"(?i)\b(implementation|professional services|deployment specialist|"
        r"technical consultant|solutions consultant|onboarding consultant|"
        r"implementation analyst|clinical deployment)\b"
    ),
    "sales-engineer": re.compile(
        r"(?i)\b(sales engineer|solutions engineer|pre[- ]?sales|"
        r"technical sales|sales engineering)\b"
    ),
    "tam": re.compile(
        r"(?i)\b(technical account|customer success|client success|partner success|"
        r"technical customer success|customer enablement|client services manager|"
        r"customer success manager|account manager)\b"
    ),
    "analyst": re.compile(
        r"(?i)\b(analyst|analytics|revenue operations|revops|business intelligence|"
        r"insights|strategy and operations|strategy & operations|operations associate)\b"
    ),
}
_VAGUE_MULTI_ROLE_TITLE = re.compile(
    r"(?i)\b(multiple roles|various roles|several roles|many roles|is hiring|"
    r"software engineers?, data engineers?|engineers?/data scientists?)\b"
)
_ROLE_TITLE_EXCLUSIONS = {
    "sales-engineer": re.compile(
        r"(?i)\b(hvac|mechanical|machinery|industrial equipment|construction "
        r"equipment|electrical equipment|building systems?|plumbing|territory)\b"
    ),
}
_LOW_QUALITY_EMPLOYER = re.compile(
    r"(?i)(@|\b(staffing|recruitment|recruiting)\b|\bteksystems\b|"
    r"\bnet2source\b|\bmichael page\b|\bselby jennings\b|\ballegis\b|"
    r"\bnogigiddy\b|\bjobot\b|\bcybercoders\b|\bmotion recruitment\b|"
    r"\binsight global\b|\bteknohire\b|\bbusiness intelli solutions\b)"
)


def _role_compatible(breakdown: Any, search_profile_id: str | None) -> bool:
    """Require the title to belong to the plan's actual role family.

    Startup bonus discovery may bypass exact search keywords, but it may not
    turn an analyst plan into a software-engineering plan.
    """
    pattern = _ROLE_TITLE_PATTERNS.get(search_profile_id or "")
    if pattern is None:
        return True
    title = str(getattr(breakdown, "title", "") or "").strip()
    if not title or _VAGUE_MULTI_ROLE_TITLE.search(title):
        return False
    exclusion = _ROLE_TITLE_EXCLUSIONS.get(search_profile_id or "")
    if exclusion is not None and exclusion.search(title):
        return False
    return bool(pattern.search(title))


def _dedupe_exact_role_identity(breakdowns: list[Any]) -> list[Any]:
    """Keep one representative per normalized company/title pair.

    This is a conservative selection-time collapse only. It does not merge or
    delete database records, so regional postings remain available for review
    and canonical duplicate clustering elsewhere in the system.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[Any] = []
    for breakdown in breakdowns:
        company = re.sub(
            r"[^a-z0-9]+", " ", str(getattr(breakdown, "company", "")).lower()
        ).strip()
        title = re.sub(
            r"[^a-z0-9]+", " ", str(getattr(breakdown, "title", "")).lower()
        ).strip()
        key = (company, title)
        if not all(key) or key not in seen:
            unique.append(breakdown)
            seen.add(key)
    return unique


def _acceptable_employer(breakdown: Any) -> bool:
    """Prefer identifiable direct employers over staffing/recruiter listings."""
    company = str(getattr(breakdown, "company", "") or "").strip()
    return bool(company) and not _LOW_QUALITY_EMPLOYER.search(company)


async def run_plan(
    *,
    tenant_id: str,
    profile_id: str = "default",
    search_profile_id: str | None = None,
    top_n: int = 10,
    dry_run: bool = False,
    auto_submit: bool = False,
    skip_previously_applied: bool = True,
    scrape_enabled: bool = True,
    # Phase 17.8 / 18.x: optional per-plan material strategy overrides.
    # ``None`` for any of these means "let the materials.generate task
    # fall back to the user's Settings → Default material strategy".
    resume_strategy: str | None = None,
    resume_template_id: str | None = None,
    resume_source_document_id: str | None = None,
    resume_patch_aggressiveness: str | None = None,
    resume_patch_allow_reorder_sections: bool | None = None,
    resume_patch_allow_add_remove_bullets: bool | None = None,
    cover_letter_strategy: str | None = None,
    cover_letter_template_id: str | None = None,
    cover_letter_source_document_id: str | None = None,
    cover_letter_patch_aggressiveness: str | None = None,
    cover_letter_patch_allow_reorder_sections: bool | None = None,
    cover_letter_patch_allow_add_remove_bullets: bool | None = None,
    search_fn: SearchFn | None = None,
    score_fn: ScoreFn | None = None,
    enqueue_fn: EnqueueFn | None = None,
    pause_root: Path | None = None,
    now: Callable[[], datetime] | None = None,
) -> PlanRunReport:
    """Execute one plan run and return a structured report.

    Args:
        tenant_id: Required. Phase 14 tenant context.
        profile_id: Applicant profile to score against. ``"default"``
            uses the YAML pointed at by ``active_profile.txt``.
        search_profile_id: Saved web-search profile. ``None`` falls
            back to the applicant ``profile_id`` (the existing
            ``search_jobs`` convention).
        top_n: Cap on jobs that reach the enqueue step. The deterministic
            scorer's `final_score` ranks the qualified pool.
        dry_run: Run search + filter but skip enqueue. ``status`` stays
            ``"ok"``; ``materials_task_ids`` / ``application_prepare_task_ids``
            stay empty.
        search_fn: Override for the search use case. Real callers leave
            this ``None`` so the real ``search_jobs`` runs.
        score_fn: Override for the scoring pipeline. Real callers leave
            this ``None``.
        enqueue_fn: Function that takes ``(task_name, payload)`` and
            returns the task id. Real callers pass a Celery
            ``send_task`` wrapper; tests pass a list-appender.
        pause_root: Override for the kill-switch sentinel root (Phase
            17.7). ``None`` uses ``PROJECT_ROOT``.
        now: Clock injection for tests.

    Returns:
        :class:`PlanRunReport` -- never raises for runtime failures
        (those are folded into ``errors``); raises
        :class:`PlanRunError` only on programmer errors.
    """
    if not tenant_id:
        raise PlanRunError("tenant_id is required")

    now_fn = now or _now_utc
    started_at = now_fn()
    run_id = str(uuid.uuid4())
    errors: list[str] = []

    # ----- 0. Kill switch (17.7) ------------------------------------
    if plan_runs_paused(pause_root):
        finished_at = now_fn()
        logger.info("plan_run paused via sentinel; run_id=%s", run_id)
        return PlanRunReport(
            run_id=run_id,
            tenant_id=tenant_id,
            profile_id=profile_id,
            search_profile_id=search_profile_id,
            status="paused",
            started_at=_isoformat(started_at),
            finished_at=_isoformat(finished_at),
            duration_seconds=(finished_at - started_at).total_seconds(),
            top_n=top_n,
            dry_run=dry_run,
        )

    # ----- 1. Search ------------------------------------------------
    del scrape_enabled  # Search currently always refreshes through search_jobs.
    search_fn = search_fn or _default_search_fn
    try:
        search_kwargs: dict[str, Any] = {
            # The AI implementation plan uses explicit saved-search filters
            # below plus the orchestration role/score gates. Skipping only its
            # intake profile preserves the broader ATS startup lane; otherwise
            # relevant stretch roles disappear before the five bonus startup
            # slots can be considered.
            "profile": (
                None
                if search_profile_id == "ai-solutions"
                else search_profile_id or profile_id
            ),
            "source": "all",
            "score": False,  # we run scoring ourselves below so we can
                             # capture the structured breakdowns
            "use_job_index": True,
            "include_views": True,
        }
        # 2026-07-08: apply the SAVED SEARCH PROFILE's filters. Previously
        # only ``profile=`` (the intake filter-profile name) was passed, so
        # the keywords / locations / experience levels / pay floor the
        # user configured in config/search_profiles.yaml were silently
        # ignored — overnight runs fetched entire company boards and
        # surfaced London / ANZ / senior roles against a Portland+Dallas
        # entry-level profile.
        search_kwargs.update(_saved_search_profile_kwargs(search_profile_id))
        search_result = await search_fn(**search_kwargs)
    except Exception as exc:  # noqa: BLE001 -- worker must keep going
        logger.exception("plan_run search failed; run_id=%s", run_id)
        finished_at = now_fn()
        errors.append(f"search: {type(exc).__name__}: {exc}")
        return PlanRunReport(
            run_id=run_id,
            tenant_id=tenant_id,
            profile_id=profile_id,
            search_profile_id=search_profile_id,
            status="error",
            started_at=_isoformat(started_at),
            finished_at=_isoformat(finished_at),
            duration_seconds=(finished_at - started_at).total_seconds(),
            top_n=top_n,
            errors=errors,
            dry_run=dry_run,
        )

    jobs = list(search_result.get("jobs") or search_result.get("items") or [])
    raw_counts = search_result.get("counts") or {}
    raw_jobs_fetched = int(raw_counts.get("raw_total") or len(jobs))
    search_filtered_out = max(0, raw_jobs_fetched - len(jobs))
    source_counts = {
        key: int(raw_counts.get(key) or 0)
        for key in ("ats", "adzuna", "hn", "remotive", "linkedin")
    }
    total_jobs_seen = len(jobs)

    # No results is a *legitimate* outcome (LinkedIn returned nothing
    # for the profile during this run). Still produce a report so the
    # digest reads "0 new jobs" rather than "missing".
    if not jobs:
        finished_at = now_fn()
        logger.info("plan_run found no jobs; run_id=%s", run_id)
        return PlanRunReport(
            run_id=run_id,
            tenant_id=tenant_id,
            profile_id=profile_id,
            search_profile_id=search_profile_id,
            status="no_results",
            started_at=_isoformat(started_at),
            finished_at=_isoformat(finished_at),
            duration_seconds=(finished_at - started_at).total_seconds(),
            top_n=top_n,
            raw_jobs_fetched=raw_jobs_fetched,
            search_filtered_out=search_filtered_out,
            source_counts=source_counts,
            total_jobs_seen=0,
            dry_run=dry_run,
        )

    # ----- 2. Score + filter ---------------------------------------
    # Production wiring needs ``tenant_id`` so it can resolve
    # ``RawJob.id`` -> ``JobPosting.id`` for the review queue rows
    # (codex P1 fix). The 2-arg ``ScoreFn`` contract stays unchanged
    # so existing test stubs are untouched.
    if score_fn is None:
        score_fn = functools.partial(_default_score_fn, tenant_id=tenant_id)
    try:
        breakdowns = score_fn(jobs, profile_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("plan_run scoring failed; run_id=%s", run_id)
        finished_at = now_fn()
        errors.append(f"score: {type(exc).__name__}: {exc}")
        return PlanRunReport(
            run_id=run_id,
            tenant_id=tenant_id,
            profile_id=profile_id,
            search_profile_id=search_profile_id,
            status="error",
            started_at=_isoformat(started_at),
            finished_at=_isoformat(finished_at),
            duration_seconds=(finished_at - started_at).total_seconds(),
            top_n=top_n,
            total_jobs_seen=total_jobs_seen,
            errors=errors,
            dry_run=dry_run,
        )

    qualified = [b for b in breakdowns if not getattr(b, "disqualified", False)]
    disqualified = total_jobs_seen - len(qualified)
    borderline = _borderline_count(breakdowns)
    role_qualified = [
        breakdown
        for breakdown in qualified
        if _role_compatible(breakdown, search_profile_id)
    ]
    role_compatible = len(role_qualified)
    employer_qualified = [
        breakdown for breakdown in role_qualified if _acceptable_employer(breakdown)
    ]
    employer_quality_rejected = role_compatible - len(employer_qualified)
    score_qualified = [
        breakdown
        for breakdown in employer_qualified
        if float(getattr(breakdown, "final_score", 0.0) or 0.0)
        >= _MIN_SELECTION_SCORE
    ]
    low_score_rejected = len(employer_qualified) - len(score_qualified)

    # Top-N already sorted descending by score_jobs. ``top_n <= 0`` is
    # an explicit "select none" -- codex P2 fix; the previous expression
    # treated 0/negative as "no cap" which silently fanned out tasks
    # for the entire qualified pool when the operator intended to
    # enqueue nothing.
    review_entry_ids: list[str] = []
    with _PLAN_SELECTION_LOCK:
        eligible = _dedupe_exact_role_identity(score_qualified)
        exact_duplicates_removed = len(score_qualified) - len(eligible)
        if skip_previously_applied:
            before_applied = len(eligible)
            eligible = _drop_previously_applied(tenant_id=tenant_id, selected=eligible)
            previously_applied_removed = before_applied - len(eligible)
        else:
            previously_applied_removed = 0
        before_pending = len(eligible)
        eligible = _drop_pending_review_jobs(tenant_id=tenant_id, selected=eligible)
        pending_deduplicated = before_pending - len(eligible)
        selected, startup_bonus_selected = _select_with_startup_bonus(
            eligible, top_n=top_n, startup_target=5
        )
        if not dry_run:
            try:
                review_entry_ids = _create_review_entries(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    selected=selected,
                )
            except Exception as exc:  # noqa: BLE001 -- non-fatal; record + continue
                logger.exception("plan_run: review_queue insert failed")
                errors.append(f"review_queue: {type(exc).__name__}: {exc}")

    # ----- 3. Persist review-queue rows + enqueue (skipped on dry_run)
    #
    # Codex P1 fix (Phase 17.2 promise): the orchestrator is the source
    # of truth for "a job is ready for human review", so it creates the
    # review_queue rows directly in the same logical step it enqueues
    # the materials task. The downstream application.prepare task body
    # is still a stub (Phase 18 / later will fill it in with the
    # form-filler agent's prepare step); leaving the review_queue
    # population to it would mean the kanban stays empty even after a
    # successful plan run.
    materials_ids: list[str] = []
    application_prepare_ids: list[str] = []
    application_submit_ids: list[str] = []
    if not dry_run:
        enqueue_fn = enqueue_fn or _default_enqueue_fn
        for breakdown in selected:
            job_id = getattr(breakdown, "job_id", None)
            if not job_id:
                errors.append("score breakdown missing job_id; skipping enqueue")
                continue
            try:
                materials_payload: dict[str, Any] = {
                    "job_id": str(job_id),
                    "profile_id": profile_id,
                    "document_types": ["resume", "cover_letter"],
                }
                # Only include override keys when the plan actually
                # provided them, so the consuming task can distinguish
                # "user didn't say" (fall back to Settings default)
                # from "user explicitly chose this".
                if resume_strategy:
                    materials_payload["resume_strategy"] = resume_strategy
                if resume_template_id:
                    materials_payload["resume_template_id"] = resume_template_id
                if resume_source_document_id:
                    materials_payload["resume_source_document_id"] = resume_source_document_id
                if resume_patch_aggressiveness:
                    materials_payload["resume_patch_aggressiveness"] = (
                        resume_patch_aggressiveness
                    )
                if resume_patch_allow_reorder_sections is not None:
                    materials_payload["resume_patch_allow_reorder_sections"] = (
                        resume_patch_allow_reorder_sections
                    )
                if resume_patch_allow_add_remove_bullets is not None:
                    materials_payload["resume_patch_allow_add_remove_bullets"] = (
                        resume_patch_allow_add_remove_bullets
                    )
                if cover_letter_strategy:
                    materials_payload["cover_letter_strategy"] = cover_letter_strategy
                if cover_letter_template_id:
                    materials_payload["cover_letter_template_id"] = cover_letter_template_id
                if cover_letter_source_document_id:
                    materials_payload["cover_letter_source_document_id"] = (
                        cover_letter_source_document_id
                    )
                if cover_letter_patch_aggressiveness:
                    materials_payload["cover_letter_patch_aggressiveness"] = (
                        cover_letter_patch_aggressiveness
                    )
                if cover_letter_patch_allow_reorder_sections is not None:
                    materials_payload["cover_letter_patch_allow_reorder_sections"] = (
                        cover_letter_patch_allow_reorder_sections
                    )
                if cover_letter_patch_allow_add_remove_bullets is not None:
                    materials_payload["cover_letter_patch_allow_add_remove_bullets"] = (
                        cover_letter_patch_allow_add_remove_bullets
                    )

                mat_id = enqueue_fn(
                    "materials.generate",
                    materials_payload,
                )
                materials_ids.append(mat_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    f"materials.generate enqueue: {type(exc).__name__}: {exc}"
                )
                continue

            try:
                # Phase 17.2 review-queue entries are now persisted
                # above; application.prepare still gets enqueued so the
                # future form-filler agent has its work item, but the
                # kanban is no longer waiting on that stub to populate.
                prep_id = enqueue_fn(
                    "application.prepare",
                    {"application_id": str(job_id)},
                )
                application_prepare_ids.append(prep_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    f"application.prepare enqueue: {type(exc).__name__}: {exc}"
                )

            if auto_submit:
                try:
                    submit_id = enqueue_fn(
                        "application.submit",
                        {"application_id": str(job_id)},
                    )
                    application_submit_ids.append(submit_id)
                except Exception as exc:  # noqa: BLE001
                    errors.append(
                        f"application.submit enqueue: {type(exc).__name__}: {exc}"
                    )

    finished_at = now_fn()
    status = "ok" if not errors else "error"
    return PlanRunReport(
        run_id=run_id,
        tenant_id=tenant_id,
        profile_id=profile_id,
        search_profile_id=search_profile_id,
        status=status,
        started_at=_isoformat(started_at),
        finished_at=_isoformat(finished_at),
        duration_seconds=(finished_at - started_at).total_seconds(),
        top_n=top_n,
        raw_jobs_fetched=raw_jobs_fetched,
        search_filtered_out=search_filtered_out,
        source_counts=source_counts,
        total_jobs_seen=total_jobs_seen,
        qualified=len(qualified),
        disqualified=disqualified,
        borderline=borderline,
        selected=len(selected),
        startup_bonus_selected=startup_bonus_selected,
        startup_selected_total=sum(
            bool(getattr(item, "is_startup", False)) for item in selected
        ),
        role_compatible=role_compatible,
        employer_quality_rejected=employer_quality_rejected,
        low_score_rejected=low_score_rejected,
        exact_duplicates_removed=exact_duplicates_removed,
        previously_applied_removed=previously_applied_removed,
        pending_deduplicated=pending_deduplicated,
        new_review_entries=len(review_entry_ids),
        selected_jobs=[
            {
                "job_id": str(getattr(item, "job_id", "")),
                "company": str(getattr(item, "company", "") or ""),
                "title": str(getattr(item, "title", "") or ""),
                "score": round(float(getattr(item, "final_score", 0.0) or 0.0), 4),
                "is_startup": bool(getattr(item, "is_startup", False)),
                "employer_type": str(
                    getattr(
                        item,
                        "employer_type",
                        "startup" if getattr(item, "is_startup", False) else "",
                    )
                    or ""
                ),
                "source": str(getattr(item, "source", "") or ""),
                "url": str(getattr(item, "url", "") or ""),
            }
            for item in selected
        ],
        materials_task_ids=materials_ids,
        application_prepare_task_ids=application_prepare_ids,
        application_submit_task_ids=application_submit_ids,
        review_entry_ids=review_entry_ids,
        errors=errors,
        dry_run=dry_run,
    )


# --------------------------------------------------------------------------- #
# Default dependency wiring                                                   #
# --------------------------------------------------------------------------- #
# These are the production wires. ``run_plan`` accepts overrides so the
# Celery task wrapper, the CLI, and the test suite can each substitute
# what they need without re-importing the orchestrator.


async def _default_search_fn(**kwargs: Any) -> dict[str, Any]:
    """Lazy import to keep this module light when scoring tests import it."""
    from src.application.jobs import search_jobs

    return await search_jobs(**kwargs)


def _saved_search_profile_kwargs(search_profile_id: str | None) -> dict[str, Any]:
    """Map a config/search_profiles.yaml entry onto search_jobs kwargs.

    Mirrors the field mapping in ``POST /api/jobs/search`` so an
    overnight plan run applies exactly the same filters the user sees
    when running the identical saved profile in the Jobs tab. Missing /
    unreadable profiles return ``{}`` (legacy behavior: board-wide
    fetch filtered only by the intake filter profile).
    """
    if not search_profile_id:
        return {}
    try:
        from src.application.search_profiles import load_search_profiles_data

        profiles = {
            entry["id"]: entry
            for entry in load_search_profiles_data().get("profiles", [])
        }
    except Exception:  # noqa: BLE001 -- config trouble -> legacy behavior
        logger.warning(
            "Saved search profile lookup failed for %r; searching unfiltered.",
            search_profile_id,
            exc_info=True,
        )
        return {}
    saved = profiles.get(search_profile_id)
    if not isinstance(saved, dict):
        logger.warning(
            "Saved search profile %r not found in search_profiles.yaml; "
            "overnight search will be unfiltered.",
            search_profile_id,
        )
        return {}

    kwargs: dict[str, Any] = {}
    if saved.get("source"):
        kwargs["source"] = saved["source"]
    if saved.get("keywords"):
        kwargs["keywords"] = list(saved["keywords"])
    for field_name in (
        "ats",
        "company",
        "time_filter",
        "pay_operator",
        "experience_operator",
    ):
        if saved.get(field_name):
            kwargs[field_name] = saved[field_name]
    for field_name in (
        "experience_levels",
        "employment_types",
        "location_types",
        "locations",
        "education_levels",
    ):
        if saved.get(field_name):
            kwargs[field_name] = list(saved[field_name])
    for field_name in ("pay_amount", "experience_years", "max_pages"):
        if saved.get(field_name) is not None:
            kwargs[field_name] = saved[field_name]
    if saved.get("location"):
        kwargs["search_location"] = saved["location"]
    return kwargs


def _coerce_job_to_rawjob(job: Any) -> Any | None:
    """Convert ``application.jobs.serialize_job`` output back to ``RawJob``.

    Codex P1 fix: ``application.jobs.search_jobs`` returns a list of
    serialized dicts (the same shape the SPA consumes), but
    ``matching.scorer.score_jobs`` expects ``RawJob`` Pydantic objects.
    Without this conversion every real plan run would crash inside
    scoring and the report would land with ``status="error"``.

    Items that are already ``RawJob`` pass through (so test stubs that
    inject raw objects keep working). Items that can't be coerced are
    dropped with a logged warning so one malformed row doesn't blank
    the whole run.
    """
    from src.application.matching import _coerce_to_raw_job  # noqa: PLC0415
    from src.intake.schema import RawJob  # noqa: PLC0415

    if isinstance(job, RawJob):
        return job
    if isinstance(job, dict):
        coerced = _coerce_to_raw_job(job)
        if coerced is None:
            logger.warning(
                "plan_run: dropping unscoreable job: id=%s company=%s",
                job.get("id"),
                job.get("company"),
            )
        return coerced
    # Some other shape (e.g. a Pydantic model from a future scraper).
    # Use it as-is and let scoring complain if it doesn't match.
    return job


def _default_score_fn(
    jobs: list[Any], profile_id: str, *, tenant_id: str = ""
) -> list[Any]:
    """Default wiring: load YAML, build scoring context, score the batch.

    Coerces serialized-dict jobs back to :class:`RawJob` first (see
    :func:`_coerce_job_to_rawjob`) so the production search path is
    actually scoreable.

    Then (codex P1 fix) resolves the persistent ``JobPosting.id`` +
    ``latest_snapshot_id`` for each scored job and overwrites
    ``breakdown.job_id`` / ``breakdown.job_snapshot_id`` so the
    pre-submit gate (which queries ``JobPosting`` by id) can find the
    row. Without this, every review entry created by a plan run would
    land with ``RawJob.id`` (a fresh UUID per scrape) and approve+submit
    would always 404 with ``missing_binding``.

    ``tenant_id`` is captured via ``functools.partial`` in
    :func:`run_plan`; the public ``ScoreFn`` contract stays 2-arg so
    test stubs are unaffected.
    """
    from src.application.profile import get_profile_path  # noqa: PLC0415
    from src.matching.scorer import build_scoring_context  # noqa: PLC0415
    from src.matching.scorer import score_jobs as score_ranked  # noqa: PLC0415
    from src.memory.profile import load_profile_yaml  # noqa: PLC0415

    path = get_profile_path(profile_id)
    if path is None or not path.exists():
        raise PlanRunError(f"profile {profile_id!r} not found at {path}")
    profile_data = load_profile_yaml(path)
    ctx = build_scoring_context(profile_data)

    raw_jobs = [_coerce_job_to_rawjob(j) for j in jobs]
    raw_jobs = [j for j in raw_jobs if j is not None]
    breakdowns = score_ranked(raw_jobs, ctx)

    metadata_by_raw_id = {
        str(job.id): {
            "is_startup": (
                job.source == "hn"
                or (job.raw_data or {}).get("employer_type") == "startup"
                or bool((job.raw_data or {}).get("startup_bonus_candidate"))
            ),
            "employer_type": str((job.raw_data or {}).get("employer_type") or ""),
            "source": str(job.source or ""),
            "url": str(job.application_url or ""),
        }
        for job in raw_jobs
    }
    for breakdown in breakdowns:
        # Dynamic metadata keeps the stable ScoreBreakdown serialization
        # contract unchanged while letting orchestration reserve bonus slots
        # and write a useful, Claude-readable selection audit.
        metadata = metadata_by_raw_id.get(str(breakdown.job_id), {})
        breakdown.is_startup = bool(metadata.get("is_startup", False))
        breakdown.employer_type = metadata.get("employer_type", "")
        breakdown.source = metadata.get("source", "")
        breakdown.url = metadata.get("url", "")

    if tenant_id:
        try:
            _resolve_and_patch_posting_ids(breakdowns, raw_jobs, tenant_id)
        except Exception:  # noqa: BLE001 - non-fatal; logged
            logger.exception(
                "plan_run: posting-id resolution failed; review entries "
                "will land with RawJob.id and pre-submit may fail"
            )

    return breakdowns


def _resolve_and_patch_posting_ids(
    breakdowns: list[Any],
    raw_jobs: list[Any],
    tenant_id: str,
) -> None:
    """Look up ``JobPosting`` by ``(tenant_id, source, source_id)`` and
    rewrite each breakdown's ``job_id`` / ``job_snapshot_id`` to the
    persisted ids.

    Operates in-place because :class:`ScoreBreakdown` is a dataclass we
    own. RawJob.id → posting.id mapping is keyed on (source, source_id)
    which is the Phase 13 ``uq_job_postings_tenant_source`` constraint.

    Misses (a posting the scorer scored but the job index never saw)
    leave the breakdown unchanged; the review entry will still be
    inserted but the pre-submit gate will report ``missing_binding``
    until the next refresh fills in the posting row. That's strictly
    better than the current behaviour (silent failure on every entry).
    """
    if not breakdowns or not raw_jobs:
        return

    from sqlalchemy import and_, or_, select  # noqa: PLC0415

    from src.core.database import get_session_factory  # noqa: PLC0415
    from src.core.models import JobPosting  # noqa: PLC0415

    # Build (source, source_id) keys from raw_jobs and index them by
    # the RawJob.id so we can map breakdown.job_id (RawJob.id as str) ->
    # source key after the DB lookup.
    rawjob_id_to_key: dict[str, tuple[str, str]] = {}
    keys: set[tuple[str, str]] = set()
    for rj in raw_jobs:
        source = getattr(rj, "source", None)
        source_id = getattr(rj, "source_id", None)
        rj_id = getattr(rj, "id", None)
        if source and source_id and rj_id is not None:
            key = (str(source), str(source_id))
            keys.add(key)
            rawjob_id_to_key[str(rj_id)] = key

    if not keys:
        return

    factory = get_session_factory()
    with factory() as session:
        rows = (
            session.execute(
                select(
                    JobPosting.id,
                    JobPosting.latest_snapshot_id,
                    JobPosting.source,
                    JobPosting.source_id,
                ).where(
                    JobPosting.tenant_id == tenant_id,
                    or_(
                        *[
                            and_(
                                JobPosting.source == s,
                                JobPosting.source_id == sid,
                            )
                            for s, sid in keys
                        ]
                    ),
                )
            )
            .all()
        )
    key_to_ids: dict[tuple[str, str], tuple[Any, Any]] = {
        (row.source, row.source_id): (row.id, row.latest_snapshot_id)
        for row in rows
    }

    for bd in breakdowns:
        rj_id = str(getattr(bd, "job_id", "") or "")
        key = rawjob_id_to_key.get(rj_id)
        if key is None:
            continue
        persisted = key_to_ids.get(key)
        if persisted is None:
            # Scored a job that was never persisted (search bypassed the
            # job index, or the row was retention-purged between scrape
            # and score). Leave the breakdown alone -- the review row
            # will still write but pre-submit will fail informatively.
            continue
        posting_id, snapshot_id = persisted
        # Mutate in place. ScoreBreakdown is a dataclass; both fields
        # are typed as ``str | None`` for job_snapshot_id (Phase 16.1)
        # and ``str`` for job_id.
        bd.job_id = str(posting_id)
        if snapshot_id is not None:
            bd.job_snapshot_id = str(snapshot_id)


def _create_review_entries(
    *,
    tenant_id: str,
    run_id: str,
    selected: list[Any],
) -> list[str]:
    """Insert one ``pending`` review_queue row per selected breakdown.

    Codex P1 fix: the Phase 17.2 promise is "the operator wakes up to
    /api/review populated with the previous run's matches". Persisting
    from the orchestrator keeps that promise true even though the
    downstream ``application.prepare`` task body is still a stub
    (Phase 18+ will wire the form-filler agent into it).

    Each entry is bound to:
      * ``job_id`` from the breakdown (Phase 13 audit link)
      * ``job_snapshot_id`` from the breakdown
      * ``run_id`` from this plan run (so the digest groups them)
      * the structured ``score_breakdown`` so the popover renders
        without re-scoring
      * denormalised ``company`` / ``title`` so the kanban renders
        without joining ``jobs``

    Returns the list of inserted entry ids (as strings).
    """
    from src.application.review import CreateEntryArgs, create_entry  # noqa: PLC0415
    from src.core.database import get_session_factory  # noqa: PLC0415

    if not selected:
        return []

    factory = get_session_factory()
    inserted: list[str] = []
    with factory() as session, session.begin():
        for breakdown in selected:
            try:
                bd_dict = (
                    breakdown.to_dict()
                    if hasattr(breakdown, "to_dict")
                    else {}
                )
            except Exception:  # noqa: BLE001 -- defensive
                bd_dict = {}
            entry = create_entry(
                session,
                CreateEntryArgs(
                    tenant_id=tenant_id,
                    job_id=getattr(breakdown, "job_id", None),
                    job_snapshot_id=getattr(breakdown, "job_snapshot_id", None),
                    materials_path=None,
                    score_breakdown=bd_dict,
                    company=getattr(breakdown, "company", None),
                    title=getattr(breakdown, "title", None),
                    run_id=run_id,
                ),
            )
            inserted.append(str(entry.id))
    return inserted


def _drop_previously_applied(*, tenant_id: str, selected: list[Any]) -> list[Any]:
    """Remove jobs that already have an application record for this tenant."""
    if not selected:
        return []

    import uuid as uuid_mod  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from src.core.database import get_session_factory  # noqa: PLC0415
    from src.core.models import Application  # noqa: PLC0415

    job_ids: list[uuid_mod.UUID] = []
    by_uuid: dict[uuid_mod.UUID, Any] = {}
    for breakdown in selected:
        try:
            job_uuid = uuid_mod.UUID(str(getattr(breakdown, "job_id", "")))
        except ValueError:
            continue
        job_ids.append(job_uuid)
        by_uuid[job_uuid] = breakdown
    if not job_ids:
        return selected

    factory = get_session_factory()
    with factory() as session:
        existing = set(
            session.execute(
                select(Application.job_id).where(
                    Application.tenant_id == tenant_id,
                    Application.job_id.in_(job_ids),
                    Application.status != "FAILED",
                )
            ).scalars()
        )
    return [bd for job_id, bd in by_uuid.items() if job_id not in existing]


def _drop_pending_review_jobs(*, tenant_id: str, selected: list[Any]) -> list[Any]:
    """Reserve queue diversity by excluding jobs already awaiting review."""
    if not selected:
        return []

    import uuid as uuid_mod  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from src.core.database import get_session_factory  # noqa: PLC0415
    from src.core.models import ReviewQueueEntry  # noqa: PLC0415

    by_uuid: dict[uuid_mod.UUID, Any] = {}
    passthrough: list[Any] = []
    for breakdown in selected:
        try:
            job_uuid = uuid_mod.UUID(str(getattr(breakdown, "job_id", "")))
        except ValueError:
            passthrough.append(breakdown)
            continue
        by_uuid[job_uuid] = breakdown
    if not by_uuid:
        return selected

    factory = get_session_factory()
    with factory() as session:
        pending = set(
            session.execute(
                select(ReviewQueueEntry.job_id).where(
                    ReviewQueueEntry.tenant_id == tenant_id,
                    ReviewQueueEntry.job_id.in_(list(by_uuid)),
                    ReviewQueueEntry.status == "pending",
                )
            ).scalars()
        )
    return passthrough + [
        breakdown for job_id, breakdown in by_uuid.items() if job_id not in pending
    ]


def _default_enqueue_fn(task_name: str, payload: dict[str, Any]) -> str:
    """Default wiring: hand off to the Phase 14 Celery app."""
    from src.tasks.app import celery_app  # noqa: PLC0415

    async_result = celery_app.send_task(task_name, kwargs=payload)
    return str(async_result.id)


__all__ = [
    "PLAN_RUN_PAUSE_SENTINEL_NAME",
    "PlanRunError",
    "PlanRunReport",
    "plan_run_pause_sentinel_path",
    "plan_runs_paused",
    "run_plan",
]
