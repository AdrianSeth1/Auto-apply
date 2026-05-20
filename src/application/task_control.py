"""Shared task-audit operations for Web and CLI controls."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.models import TaskRecord
from src.tasks import celery_app
from src.tasks.context import tenant_header_name


class TaskControlError(Exception):
    """Raised when an operator action is invalid for the task state."""


def list_task_records(
    session: Session,
    *,
    tenant_id: str,
    limit: int,
    status: str | None = None,
    kind: str | None = None,
    since: datetime | None = None,
) -> list[TaskRecord]:
    stmt = (
        select(TaskRecord)
        .where(TaskRecord.tenant_id == tenant_id)
        .order_by(TaskRecord.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(TaskRecord.status == status)
    if kind:
        stmt = stmt.where(TaskRecord.kind == kind)
    if since:
        stmt = stmt.where(TaskRecord.created_at >= since)
    return list(session.execute(stmt).scalars())


def resolve_task_record(session: Session, ident: str) -> TaskRecord | None:
    try:
        uid = uuid.UUID(ident)
    except ValueError:
        stmt = select(TaskRecord).where(TaskRecord.celery_task_id == ident).limit(1)
        return session.execute(stmt).scalar_one_or_none()
    return session.get(TaskRecord, uid)


def require_task_for_tenant(
    session: Session,
    ident: str,
    *,
    tenant_id: str,
) -> TaskRecord | None:
    row = resolve_task_record(session, ident)
    if row is None or row.tenant_id != tenant_id:
        return None
    return row


def cancel_task_record(row: TaskRecord) -> TaskRecord:
    if row.status != "queued":
        raise TaskControlError(f"only queued tasks may be cancelled; got {row.status}")
    if row.celery_task_id:
        try:
            celery_app.control.revoke(row.celery_task_id, terminate=False)
        except Exception:  # noqa: BLE001 -- audit state must still record intent
            pass
    row.status = "cancelled"
    row.updated_at = datetime.now(UTC)
    return row


def retry_task_record(row: TaskRecord) -> dict[str, Any]:
    if row.status not in {"failed", "cancelled"}:
        raise TaskControlError(
            f"only failed/cancelled tasks may be retried; got {row.status}"
        )
    result = celery_app.send_task(
        row.kind,
        kwargs=row.payload or {},
        queue=row.queue,
        headers={tenant_header_name(): row.tenant_id},
    )
    return {
        "retried": str(row.id),
        "kind": row.kind,
        "celery_task_id": str(getattr(result, "id", "") or "") or None,
    }


__all__ = [
    "TaskControlError",
    "cancel_task_record",
    "list_task_records",
    "require_task_for_tenant",
    "resolve_task_record",
    "retry_task_record",
]
