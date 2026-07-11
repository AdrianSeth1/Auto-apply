"""Celery Beat schedule (Phase 14.5).

Replaces the prior APScheduler plan (D021, now superseded by D025).
Beat only enqueues -- business logic never runs in the Beat process.
The redbeat scheduler (configured in :mod:`src.tasks.app` via
``redbeat_redis_url``) provides leader election so multi-instance Beat
does not double-fire (Phase 14.10).

Schedule design:

* ``daily_search`` -- cron-driven saved-search refresh. Phase 17 will
  fan this out into per-source ``search.refresh`` children; for now
  the schedule entry exists so 14.7 ``autoapply schedule list`` has
  something to display.
* ``jd_health_check`` -- drives the Phase 13.3 freshness state
  machine's time decay (``project_by_time``: ``active→stale @ 24h``,
  ``stale→unknown @ 72h``, ``unknown→expired @ 7d``). Hourly granularity
  is sufficient since the decay tiers are hours / days.
* ``application_status_sync`` -- currently unscheduled. Future work should
  poll supported ATS / application portals first, then add email / HR-reply
  ingestion.
* ``linkedin_cookie_refresh`` -- daily refresh while the cookie is
  still warm.
* ``cache_eviction`` -- hourly L1+L2 cache hygiene (drops keys whose
  TTL is technically alive but whose underlying source has changed).
* ``gate_expire_sweep`` -- 15-minute sweep that flips
  ``gate_queue`` rows past their TTL to ``expired``.

All entries default to the ``maintenance`` queue; per-task overrides
go through ``options={'queue': '...'}`` so tasks like the search fan-out
land on the ``search`` queue.

This module is import-safe (no Beat lock acquired here); calling
:func:`get_schedule` returns the dict that ``celery beat`` consumes.
"""

from __future__ import annotations

from celery.schedules import crontab

from src.application.automation_plans import automation_plan_schedule_entries

# Per-task options to route Beat-enqueued task to the right queue.
_SEARCH_OPTS: dict[str, object] = {"queue": "search"}
_MAINTENANCE_OPTS: dict[str, object] = {"queue": "maintenance"}
# Phase 17.1: the plan_run orchestrator is heavy (it fans out into
# materials/application tasks) and belongs on the search queue so
# materials workers don't block on it.
_ORCHESTRATION_OPTS: dict[str, object] = {"queue": "search"}


def get_schedule() -> dict[str, dict[str, object]]:
    """Return the Beat schedule. Kept as a function so callers can
    monkey-patch the entries in tests without mutating module state."""
    schedule = {
        "daily_search": {
            "task": "search.daily_fanout",
            "schedule": crontab(hour=2, minute=0),  # 02:00 UTC every day
            "options": _SEARCH_OPTS,
        },
        # Phase 17.1: default end-to-end batch run. User-created task
        # schedules are merged below from config/automation_plans.yaml.
        # All kwargs default-friendly so a fresh install with no
        # search_profile_id still produces a report.
        "plan_run": {
            "task": "orchestration.plan_run",
            "schedule": crontab(hour=23, minute=0),
            "options": _ORCHESTRATION_OPTS,
        },
        # Phase 17.6: morning digest. 2026-07-07: moved 08:00 -> 12:00 UTC
        # so it lands at 7am Central (the user's morning) instead of 3am,
        # right after the overnight automation plans (08:00-09:00 UTC)
        # have finished generating materials. Routed to the maintenance
        # queue since it's cheap (one DB query + a directory scan).
        "morning_digest": {
            "task": "notifications.morning_digest",
            "schedule": crontab(hour=12, minute=0),
            "options": _MAINTENANCE_OPTS,
        },
        "jd_health_check": {
            "task": "maintenance.jd_health_check",
            "schedule": crontab(minute=0),  # every hour, on the hour
            "options": _MAINTENANCE_OPTS,
        },
        # Phase 18.1: ``application_status_sync`` removed from Beat
        # because ``maintenance.status_sync`` is explicitly
        # ``not_implemented``. Restore it when ATS/application-portal
        # polling lands.
        # 2026-07-07: email reply ingestion (the "second status source"
        # anticipated above). The task no-ops with a structured result
        # when email.enabled is false, so the entry is safe on fresh
        # installs. Every 6 hours — recruiter mail isn't latency-critical.
        "email_ingest": {
            "task": "maintenance.email_ingest",
            "schedule": crontab(minute=15, hour="*/6"),
            "options": _MAINTENANCE_OPTS,
        },
        # 2026-07-10: ``linkedin_cookie_refresh`` REMOVED from Beat after
        # LinkedIn flagged the user's account for automated profile-data
        # access (rehabRestrictionChallenge warning). No scheduled task
        # may touch LinkedIn; search profiles are ATS-only. The task
        # remains registered for manual CLI use only, and should stay
        # unscheduled unless the user explicitly accepts the account
        # risk of resuming automated access.
        "cache_eviction": {
            "task": "maintenance.cache_eviction",
            "schedule": crontab(minute=30),  # every hour, on :30
            "options": _MAINTENANCE_OPTS,
        },
        "gate_expire_sweep": {
            "task": "maintenance.gate_expire_sweep",
            "schedule": crontab(minute="*/15"),
            "options": _MAINTENANCE_OPTS,
        },
    }
    schedule.update(automation_plan_schedule_entries())
    return schedule


# Phase 17 dashboard polish: human-facing metadata for each Beat
# entry. ``user_facing=True`` flags entries the operator cares about
# day-to-day (plan_run / morning_digest); the rest are background
# maintenance and the UI collapses them by default. Keep this dict in
# sync with ``get_schedule()`` above.
SCHEDULE_DISPLAY: dict[str, dict[str, object]] = {
    "daily_search": {
        "display_name": "Job discovery refresh",
        "description": "Refreshes saved searches at 02:00 UTC and updates the Job Index.",
        "user_facing": True,
    },
    "plan_run": {
        "display_name": "Application batch run",
        "description": (
            "Runs search, scoring, materials generation, and application prep."
        ),
        "user_facing": True,
    },
    "morning_digest": {
        "display_name": "Morning digest",
        "description": (
            "Builds the dashboard digest at 08:00 UTC with batch run and review counts."
        ),
        "user_facing": True,
    },
    "jd_health_check": {
        "display_name": "JD freshness check",
        "description": (
            "Advances posting freshness from active to stale, unknown, or expired."
        ),
        "user_facing": False,
    },
    "application_status_sync": {
        "display_name": "Application status sync",
        "description": "Syncs submitted application outcomes every 6 hours.",
        "user_facing": False,
    },
    "linkedin_cookie_refresh": {
        "display_name": "LinkedIn cookie refresh",
        "description": "Refreshes the LinkedIn session daily at 03:00 UTC.",
        "user_facing": False,
    },
    "cache_eviction": {
        "display_name": "Cache eviction",
        "description": "Removes stale cache keys every hour.",
        "user_facing": False,
    },
    "gate_expire_sweep": {
        "display_name": "Approval timeout sweep",
        "description": "Expires unresolved approval requests every 15 minutes.",
        "user_facing": False,
    },
}


def install(app) -> None:
    """Bind the schedule onto the given Celery app + select the
    redbeat scheduler. Idempotent."""
    app.conf.beat_schedule = get_schedule()
    app.conf.beat_scheduler = "redbeat.RedBeatScheduler"


__all__ = ["get_schedule", "install", "SCHEDULE_DISPLAY", "TASK_KIND_DISPLAY"]


# Map of Celery task `kind` strings to display names + short
# descriptions. Surfaced by /api/tasks so the operator UI does not show
# raw values like `search.daily_fanout`. Keep in sync with the kinds
# registered in src/tasks/tasks.py.
TASK_KIND_DISPLAY: dict[str, dict[str, str]] = {
    "search.daily_fanout": {
        "display_name": "Search fan-out",
        "description": "Splits saved searches into source-specific refresh jobs.",
    },
    "search.refresh": {
        "display_name": "Search refresh",
        "description": "Runs one source search and writes results to the index.",
    },
    "jobs.enrich": {
        "display_name": "Job enrichment",
        "description": "Fetches and snapshots the full job description for one posting.",
    },
    "materials.generate": {
        "display_name": "Materials generation",
        "description": "Generates or revises resume and cover letter materials.",
    },
    "application.prepare": {
        "display_name": "Application prep",
        "description": "Assembles generated materials and form data for review.",
    },
    "application.submit": {
        "display_name": "Application submit",
        "description": "Submits an approved application after the pre-submit gate clears.",
    },
    "maintenance.status_sync": {
        "display_name": "Application status sync",
        "description": "Syncs HR replies and rejection status.",
    },
    "maintenance.jd_health_check": {
        "display_name": "JD freshness check",
        "description": "Advances the active/stale/unknown/expired freshness state.",
    },
    "maintenance.linkedin_cookie_refresh": {
        "display_name": "LinkedIn cookie refresh",
        "description": "Keeps the LinkedIn session warm.",
    },
    "maintenance.cache_eviction": {
        "display_name": "Cache eviction",
        "description": "Removes stale cache keys.",
    },
    "maintenance.gate_expire_sweep": {
        "display_name": "Approval timeout sweep",
        "description": "Expires unresolved approval requests.",
    },
    "orchestration.plan_run": {
        "display_name": "Application batch run",
        "description": "Runs search, scoring, materials, and application prep.",
    },
    "notifications.morning_digest": {
        "display_name": "Morning digest",
        "description": "Builds the dashboard digest data.",
    },
}
