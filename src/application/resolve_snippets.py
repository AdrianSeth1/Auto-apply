"""Phase S4 / SUP-07: batch driver for ``src.intake.full_jd_resolver``.

Finds *promising* snippet-only Adzuna postings in the legacy ``jobs`` table:
the latest immutable evaluation must already clear target routing, location,
and every known hard eligibility gate, while meeting the existing Tier-B
review/role floors. This prevents broad recovery requests for snippets that
cannot become A/B jobs. It then attempts recovery through ``resolve_full_jd``
and, on success, writes the result to **both** places this codebase currently
keeps job content.

- the legacy ``jobs`` row's ``description``/``raw_data`` are updated in
  place (that table has no snapshot/immutability model to begin with);
- if the same ``(source, source_id)`` already has a Job Index posting
  (every job that has gone through a V2 portfolio run does -- see
  ``src.orchestration.portfolio_run.run_portfolio_v2``), a **new immutable
  JobSnapshot** is written via ``src.jobs.enrich.enrich_posting`` -- the same
  call convention ``portfolio_run.py`` already uses, never a hand-rolled
  insert. If no Job Index posting exists yet for this job, nothing is
  created here; the next portfolio run that surfaces it will index it (with
  the now-recovered content already sitting in the legacy row, since Adzuna
  ingestion reads from the live API each time, not from this table).

Every attempt -- success or failure -- is recorded back onto the legacy
row's ``raw_data`` (``full_jd_recovery_attempts``, `..._last_reason``,
``..._last_attempted_at``) so a permanently-unresolvable posting (wrong
target, disabled adapter) is not retried forever. ``max_attempts`` bounds
this the same way ``config/source_policy.yaml``'s health section bounds
endpoint retries conceptually, though this is a separate, simpler counter,
not wired into that quarantine state machine.

This never fabricates a snapshot from a failed attempt, never enables an
adapter the resolver itself would reject, and never rescinds an existing
snapshot -- it only ever adds a new one on a genuine recovery.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import yaml
from sqlalchemy import Float, Integer, cast, exists, func, select
from sqlalchemy.orm import Session

from src.core.config import PROJECT_ROOT
from src.core.models import (
    Job,
    JobEvaluationReason,
    JobPosting,
    JobSnapshot,
    JobTargetEvaluation,
)
from src.intake.full_jd_resolver import resolve_full_jd
from src.intake.schema import JobRequirements, RawJob
from src.jobs.enrich import enrich_posting
from src.jobs.store import JobIndexStore

logger = logging.getLogger("autoapply.application.resolve_snippets")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BATCH_LIMIT = 50
DEFAULT_MIN_REVIEW_INDEX = 58.0
_MIN_ROLE_SCORE = 60.0


@dataclass
class ResolveSnippetsSummary:
    considered: int = 0
    recovered: int = 0
    failed: int = 0
    skipped_max_attempts: int = 0
    failure_reasons: dict[str, int] = field(default_factory=dict)


def load_source_policy(path: Any | None = None) -> dict:
    """Load ``config/source_policy.yaml``. Returns ``{}`` (fail closed --
    nothing is treated as enabled) if the file is missing or malformed,
    never raises."""
    policy_path = path or (PROJECT_ROOT / "config" / "source_policy.yaml")
    try:
        with open(policy_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("resolve_snippets: could not load source_policy.yaml: %s", exc)
        return {}


def _pending_snippets_query(
    *,
    max_attempts: int,
    limit: int,
    minimum_review_index: float = DEFAULT_MIN_REVIEW_INDEX,
):
    """Promising rows eligible for a recovery attempt.

    ``func.lower(func.trim(...))`` matches the case-insensitive-comparison
    invariant used elsewhere against this table (AGENTS.md item 7).
    """
    attempts_col = Job.raw_data["full_jd_recovery_attempts"].astext
    route_pass = exists(
        select(1).where(
            JobEvaluationReason.evaluation_id == JobTargetEvaluation.id,
            JobEvaluationReason.stage == "target_routing",
            JobEvaluationReason.decision == "pass",
        )
    )
    location_pass = exists(
        select(1).where(
            JobEvaluationReason.evaluation_id == JobTargetEvaluation.id,
            JobEvaluationReason.stage == "global_eligibility",
            JobEvaluationReason.decision == "pass",
            JobEvaluationReason.details["gate_id"].astext == "location",
        )
    )
    blocking_gate = exists(
        select(1).where(
            JobEvaluationReason.evaluation_id == JobTargetEvaluation.id,
            JobEvaluationReason.stage == "global_eligibility",
            JobEvaluationReason.decision == "fail",
        )
    )
    promising_evaluation = exists(
        select(1)
        .select_from(JobPosting)
        .join(JobSnapshot, JobSnapshot.id == JobPosting.latest_snapshot_id)
        .join(
            JobTargetEvaluation,
            JobTargetEvaluation.snapshot_id == JobSnapshot.id,
        )
        .where(
            JobPosting.tenant_id == Job.tenant_id,
            func.lower(func.trim(JobPosting.source)) == "adzuna",
            JobPosting.source_id == Job.source_id,
            JobTargetEvaluation.review_index >= minimum_review_index,
            cast(JobTargetEvaluation.component_scores["role"].astext, Float) >= _MIN_ROLE_SCORE,
            route_pass,
            location_pass,
            ~blocking_gate,
        )
        .correlate(Job)
    )
    return (
        select(Job)
        .where(func.lower(func.trim(Job.source)) == "adzuna")
        .where(Job.raw_data["description_completeness"].astext == "snippet")
        .where(Job.raw_data["full_jd_recovered"].astext.is_(None))
        .where(Job.application_url.is_not(None))
        .where((attempts_col.is_(None)) | (cast(attempts_col, Integer) < max_attempts))
        .where(promising_evaluation)
        .order_by(Job.discovered_at.desc())
        .limit(limit)
    )


def _raw_job_from_row(row: Job) -> RawJob:
    requirements = row.requirements or {}
    try:
        parsed_requirements = JobRequirements(**requirements)
    except (TypeError, ValueError):
        parsed_requirements = JobRequirements()
    return RawJob(
        source=(row.source or "adzuna").strip().lower(),
        source_id=row.source_id or "",
        company=row.company,
        title=row.title,
        location=row.location,
        employment_type=row.employment_type or "unknown",
        seniority=row.seniority or "unknown",
        description=row.description,
        requirements=parsed_requirements,
        application_url=row.application_url,
        ats_type=row.ats_type or "unknown",
        raw_data=row.raw_data or {},
        discovered_at=row.discovered_at or datetime.now(UTC),
        expires_at=row.expires_at,
    )


def resolve_pending_snippets(
    session: Session,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    limit: int = DEFAULT_BATCH_LIMIT,
    minimum_review_index: float = DEFAULT_MIN_REVIEW_INDEX,
    source_policy: dict | None = None,
) -> ResolveSnippetsSummary:
    """Attempt recovery for up to ``limit`` pending snippet-only postings.

    Caller controls the transaction (matches ``JobIndexStore``'s contract):
    call inside ``with session.begin(): ...`` so a mid-batch failure doesn't
    leave a partially-committed batch.
    """
    policy = source_policy if source_policy is not None else load_source_policy()
    summary = ResolveSnippetsSummary()

    rows = list(
        session.execute(
            _pending_snippets_query(
                max_attempts=max_attempts,
                limit=limit,
                minimum_review_index=minimum_review_index,
            )
        ).scalars()
    )
    for row in rows:
        store = JobIndexStore(session, tenant_id=row.tenant_id)
        summary.considered += 1
        raw_job = _raw_job_from_row(row)
        outcome = resolve_full_jd(raw_job, source_policy=policy)

        attempts = int((row.raw_data or {}).get("full_jd_recovery_attempts") or 0) + 1
        attempt_metadata = {
            "full_jd_recovery_attempts": attempts,
            "full_jd_recovery_last_attempted_at": datetime.now(UTC).isoformat(),
        }

        if not outcome.resolved:
            summary.failed += 1
            summary.failure_reasons[outcome.reason or "unknown"] = (
                summary.failure_reasons.get(outcome.reason or "unknown", 0) + 1
            )
            row.raw_data = {
                **(row.raw_data or {}),
                **attempt_metadata,
                "full_jd_recovery_last_reason": outcome.reason,
            }
            continue

        recovered = outcome.job
        assert recovered is not None  # resolved=True always carries a job
        row.description = recovered.description
        row.raw_data = {
            **(recovered.raw_data or {}),
            **attempt_metadata,
            "full_jd_recovery_last_reason": "resolved",
        }
        summary.recovered += 1

        # Job Index: only enrich a posting that already exists there. A
        # plain SELECT, deliberately NOT store.upsert_posting() -- that
        # method creates a bare posting row if none exists, which would
        # silently index this job into the Job Index a run early, before
        # any real V2 discovery run has done so. If nothing comes back
        # here, leave the Job Index untouched; the next portfolio run that
        # surfaces this job will index it there for the first time, and it
        # will already see the recovered content in the legacy row above.
        normalized_source = (recovered.source or "adzuna").strip().lower()
        existing_posting = session.execute(
            select(JobPosting).where(
                JobPosting.tenant_id == store.tenant_id,
                func.lower(func.trim(JobPosting.source)) == normalized_source,
                JobPosting.source_id == (row.source_id or ""),
            )
        ).scalar_one_or_none()
        if existing_posting is not None:
            content = {
                "title": recovered.title,
                "location": recovered.location,
                "employment_type": recovered.employment_type,
                "seniority": recovered.seniority,
                "description": recovered.description,
                "requirements": recovered.requirements.model_dump(mode="json"),
                "application_url": recovered.application_url,
                "raw_data": recovered.raw_data,
            }
            enriched = enrich_posting(
                store=store,
                source=recovered.source,
                source_id=recovered.source_id,
                company=recovered.company,
                content=content,
            )
            if enriched.content_changed and recovered.provenance is not None:
                snapshot = session.get(JobSnapshot, enriched.snapshot_id)
                if snapshot is not None:
                    snapshot.provenance = recovered.provenance.model_dump(mode="json")

    if rows:
        logger.info(
            "resolve_pending_snippets: considered=%d recovered=%d failed=%d",
            summary.considered,
            summary.recovered,
            summary.failed,
        )
    return summary


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MIN_REVIEW_INDEX",
    "ResolveSnippetsSummary",
    "load_source_policy",
    "resolve_pending_snippets",
]
