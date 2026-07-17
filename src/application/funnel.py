"""Durable, idempotent job-search funnel milestones.

Stages are intentionally business-facing: discovered, qualified, reviewed,
applied, screen, interview, offer. Events are append-only and deduplicated by
entity/stage, so retries and repeated searches do not inflate conversion rates.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert

from src.core.models import TENANT_DEFAULT, FunnelEvent

FUNNEL_STAGES = (
    "discovered",
    "qualified",
    "reviewed",
    "applied",
    "screen",
    "interview",
    "offer",
)
BUSINESS_STAGES = ("surfaced", "reviewed", "applied", "screen", "interview", "offer")


def record_event(
    session,
    *,
    entity_type: str,
    entity_id,
    stage: str,
    job_id=None,
    posting_id=None,
    application_id=None,
    evaluation_id=None,
    journey_key: str | None = None,
    source: str | None = None,
    profile_variant: str | None = None,
    material_variant: str | None = None,
    time_spent_seconds: int | None = None,
    metadata: dict | None = None,
    occurred_at: datetime | None = None,
    tenant_id: str = TENANT_DEFAULT,
) -> None:
    if stage not in (*FUNNEL_STAGES, "surfaced"):
        raise ValueError(f"Unsupported funnel stage: {stage}")
    entity_uuid = entity_id if isinstance(entity_id, uuid.UUID) else uuid.UUID(str(entity_id))
    values = {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "entity_type": entity_type,
        "entity_id": entity_uuid,
        "stage": stage,
        "job_id": job_id,
        "posting_id": posting_id,
        "application_id": application_id,
        "evaluation_id": evaluation_id,
        "journey_key": journey_key or (str(evaluation_id) if evaluation_id else None),
        "source": source,
        "profile_variant": profile_variant,
        "material_variant": material_variant,
        "time_spent_seconds": time_spent_seconds,
        "event_metadata": metadata,
        "occurred_at": occurred_at or datetime.now(UTC),
    }
    stmt = insert(FunnelEvent).values(**values).on_conflict_do_nothing(
        constraint="uq_funnel_event_stage"
    )
    session.execute(stmt)


def record_search_events(fetched_jobs: list, qualified_jobs: list) -> None:
    """Persist discovered/qualified milestones after a completed search."""
    from src.core.database import get_session_factory

    qualified_ids = {str(job.id) for job in qualified_jobs if not job.raw_data.get("disqualified")}
    with get_session_factory()() as session:
        for job in fetched_jobs:
            profile = (job.raw_data or {}).get("best_profile")
            common = {
                "entity_type": "job",
                "entity_id": job.id,
                "job_id": job.id,
                "source": str(job.source),
                "profile_variant": profile,
                "metadata": {"company": job.company, "title": job.title},
            }
            record_event(session, stage="discovered", occurred_at=job.discovered_at, **common)
            if str(job.id) in qualified_ids:
                record_event(session, stage="qualified", **common)
        session.commit()


def weekly_funnel(*, weeks: int = 12) -> dict:
    """Return weekly stage counts and conversion rates for dashboard views."""
    from src.core.database import get_session_factory

    weeks = max(1, min(int(weeks), 52))
    start = datetime.now(UTC) - timedelta(weeks=weeks)
    with get_session_factory()() as session:
        rows = (
            session.query(FunnelEvent)
            .filter(FunnelEvent.tenant_id == TENANT_DEFAULT, FunnelEvent.occurred_at >= start)
            .order_by(FunnelEvent.occurred_at)
            .all()
        )

    buckets: dict[str, dict] = {}
    dimensions = {"source": {}, "profile_variant": {}, "material_variant": {}}
    for event in rows:
        monday = (event.occurred_at - timedelta(days=event.occurred_at.weekday())).date()
        label = monday.isoformat()
        bucket = buckets.setdefault(label, {stage: 0 for stage in FUNNEL_STAGES})
        if event.stage in bucket:
            bucket[event.stage] += 1
        for name in dimensions:
            value = getattr(event, name) or "unknown"
            dim_bucket = dimensions[name].setdefault(value, {stage: 0 for stage in FUNNEL_STAGES})
            if event.stage in dim_bucket:
                dim_bucket[event.stage] += 1

    def finalize(counts: dict) -> dict:
        result = dict(counts)
        result["conversion"] = {}
        for previous, current in zip(FUNNEL_STAGES, FUNNEL_STAGES[1:]):
            denominator = counts[previous]
            result["conversion"][f"{previous}_to_{current}"] = (
                round(counts[current] / denominator, 3) if denominator else 0.0
            )
        return result

    journeys: dict[str, dict] = {}
    for event in rows:
        if event.evaluation_id is None:
            continue
        key = str(event.evaluation_id)
        journey = journeys.setdefault(key, {"evaluation_id": key, "events": {}})
        journey["events"].setdefault(event.stage, event.occurred_at)
    cohort_buckets: dict[str, dict[str, int]] = {}
    for journey in journeys.values():
        surfaced = journey["events"].get("surfaced")
        if surfaced is None:
            continue
        monday = (surfaced - timedelta(days=surfaced.weekday())).date().isoformat()
        counts = cohort_buckets.setdefault(monday, {stage: 0 for stage in BUSINESS_STAGES})
        for stage in BUSINESS_STAGES:
            if stage in journey["events"]:
                counts[stage] += 1

    def finalize_business(counts: dict[str, int]) -> dict:
        result = dict(counts)
        denominator = counts["surfaced"]
        result["conversion_from_surfaced"] = {
            stage: (round(counts[stage] / denominator, 3) if denominator else 0.0)
            for stage in BUSINESS_STAGES[1:]
        }
        return result

    return {
        "ok": True,
        "stages": list(FUNNEL_STAGES),
        "weeks": [{"week_start": key, **finalize(buckets[key])} for key in sorted(buckets)],
        "by_source": [
            {"source": key, **finalize(value)}
            for key, value in sorted(dimensions["source"].items())
        ],
        "by_profile": [
            {"profile": key, **finalize(value)}
            for key, value in sorted(dimensions["profile_variant"].items())
        ],
        "by_material_variant": [
            {"material_variant": key, **finalize(value)}
            for key, value in sorted(dimensions["material_variant"].items())
        ],
        "v2_surfaced_cohorts": [
            {"week_start": week, **finalize_business(cohort_buckets[week])}
            for week in sorted(cohort_buckets)
        ],
        "legacy_note": "Events without a confident evaluation link are excluded from V2 cohort conversion.",
    }
