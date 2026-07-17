"""Shared Beat schedule dispatch operations for Web and CLI controls."""

from __future__ import annotations

from typing import Any

from src.tasks import celery_app
from src.tasks.beat import SCHEDULE_DISPLAY, get_schedule
from src.tasks.context import tenant_header_name


class ScheduleEntryNotFound(Exception):
    """Raised when a schedule entry is unknown or hidden from this surface."""


class ScheduleEntryContractError(ValueError):
    """Raised before publish when a task name and payload shape disagree."""


def _validate_orchestration_contract(task_name: str, kwargs: dict[str, Any]) -> None:
    is_v2_payload = (
        "target_ids" in kwargs
        or kwargs.get("pipeline_version") in {"v2_shadow", "v2"}
    )
    if task_name == "orchestration.plan_run" and is_v2_payload:
        raise ScheduleEntryContractError(
            "V2 automation payload cannot be dispatched to orchestration.plan_run"
        )
    if task_name == "orchestration.portfolio_run":
        if kwargs.get("pipeline_version") not in {"v2_shadow", "v2"}:
            raise ScheduleEntryContractError(
                "orchestration.portfolio_run requires pipeline_version v2_shadow or v2"
            )


def dispatch_schedule_entry(entry: dict[str, Any], *, tenant_id: str) -> dict[str, str]:
    task_name = str(entry["task"])
    queue = str(entry.get("options", {}).get("queue", "maintenance"))
    kwargs = entry.get("kwargs") or {}
    _validate_orchestration_contract(task_name, kwargs)
    result = celery_app.send_task(
        task_name,
        kwargs=kwargs,
        queue=queue,
        headers={tenant_header_name(): tenant_id},
    )
    response = {"enqueued": task_name, "queue": queue}
    task_id = getattr(result, "id", None)
    if task_id:
        response["task_id"] = str(task_id)
    return response


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
    "ScheduleEntryContractError",
    "ScheduleEntryNotFound",
    "dispatch_schedule_entry",
    "run_schedule_entry_now",
]
