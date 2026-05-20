"""Shared Beat schedule dispatch operations for Web and CLI controls."""

from __future__ import annotations

from typing import Any

from src.tasks import celery_app
from src.tasks.beat import SCHEDULE_DISPLAY, get_schedule
from src.tasks.context import tenant_header_name


class ScheduleEntryNotFound(Exception):
    """Raised when a schedule entry is unknown or hidden from this surface."""


def dispatch_schedule_entry(entry: dict[str, Any], *, tenant_id: str) -> dict[str, str]:
    task_name = str(entry["task"])
    queue = str(entry.get("options", {}).get("queue", "maintenance"))
    celery_app.send_task(
        task_name,
        kwargs=entry.get("kwargs") or {},
        queue=queue,
        headers={tenant_header_name(): tenant_id},
    )
    return {"enqueued": task_name, "queue": queue}


def run_schedule_entry_now(
    name: str,
    *,
    tenant_id: str,
    user_facing_only: bool = False,
) -> dict[str, str]:
    schedule = get_schedule()
    meta = SCHEDULE_DISPLAY.get(name, {})
    if name not in schedule or (
        user_facing_only and not bool(meta.get("user_facing", False))
    ):
        raise ScheduleEntryNotFound(f"no such schedule entry: {name}")
    return dispatch_schedule_entry(schedule[name], tenant_id=tenant_id)


__all__ = [
    "ScheduleEntryNotFound",
    "dispatch_schedule_entry",
    "run_schedule_entry_now",
]
