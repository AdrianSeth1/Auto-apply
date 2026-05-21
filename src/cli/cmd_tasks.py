"""``autoapply tasks ...`` (Phase 14.7).

Operator-facing inspection + retry / cancel commands. Reads the Phase
14.2 audit table; does NOT consult Celery's transient result backend.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import click
from sqlalchemy.orm import Session, sessionmaker

from src.application.task_control import (
    TaskControlError,
    cancel_task_record,
    list_task_records,
    resolve_task_record,
    retry_task_record,
)
from src.core.config import load_config
from src.core.database import get_engine
from src.core.models import TENANT_DEFAULT, TaskRecord
from src.tasks.tasks import KNOWN_TASK_NAMES


def _session() -> Session:
    factory = sessionmaker(bind=get_engine(load_config()))
    return factory()


@click.group("tasks")
def tasks_cmd() -> None:
    """Inspect, retry, and cancel AutoApply task runs."""


@tasks_cmd.command("list")
@click.option("--limit", default=20, type=click.IntRange(min=1, max=500))
@click.option(
    "--status",
    type=click.Choice(
        ["queued", "running", "waiting_human", "succeeded", "failed", "cancelled"]
    ),
    default=None,
)
@click.option("--kind", default=None, help="Filter by task kind (e.g. materials.generate).")
@click.option("--tenant", default=TENANT_DEFAULT, help="Tenant id to inspect.")
@click.option("--since", default=None, help="ISO timestamp or '24h' / '7d' shorthand.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def tasks_list(
    limit: int,
    status: str | None,
    kind: str | None,
    tenant: str,
    since: str | None,
    as_json: bool,
) -> None:
    """Show recent task rows from the audit table."""
    session = _session()
    try:
        rows = list_task_records(
            session,
            tenant_id=(tenant or TENANT_DEFAULT).strip() or TENANT_DEFAULT,
            limit=limit,
            status=status,
            kind=kind,
            since=_parse_since(since) if since else None,
        )
    finally:
        session.close()

    if as_json:
        click.echo(json.dumps([_row_to_dict(r) for r in rows], default=str))
        return
    if not rows:
        click.echo("no tasks found")
        return
    click.echo(f"{'id':<36}  {'status':<14}  {'kind':<22}  {'queue':<12}  created_at")
    for r in rows:
        click.echo(
            f"{r.id!s:<36}  {r.status:<14}  {r.kind:<22}  {r.queue:<12}  {r.created_at.isoformat()}"
        )


@tasks_cmd.command("inspect")
@click.argument("task_id")
def tasks_inspect(task_id: str) -> None:
    """Show the full audit row for one task (by UUID or celery_task_id)."""
    session = _session()
    try:
        row = resolve_task_record(session, task_id)
        if row is None:
            click.echo(f"no task matches '{task_id}'", err=True)
            raise SystemExit(1)
        click.echo(json.dumps(_row_to_dict(row), default=str, indent=2))
    finally:
        session.close()


@tasks_cmd.command("retry")
@click.argument("task_id")
def tasks_retry(task_id: str) -> None:
    """Re-enqueue a failed task using its original payload."""
    session = _session()
    try:
        row = resolve_task_record(session, task_id)
        if row is None:
            click.echo(f"no task matches '{task_id}'", err=True)
            raise SystemExit(1)
        try:
            retry_task_record(row)
        except TaskControlError as exc:
            click.echo(
                f"refusing to retry task {row.id}: {exc}",
                err=True,
            )
            raise SystemExit(2)
        click.echo(f"retried {row.id} ({row.kind})")
    finally:
        session.close()


@tasks_cmd.command("cancel")
@click.argument("task_id")
def tasks_cancel(task_id: str) -> None:
    """Mark a queued task as cancelled. Does not interrupt a task that
    is already ``running`` (use Celery revoke for that)."""
    session = _session()
    try:
        row = resolve_task_record(session, task_id)
        if row is None:
            click.echo(f"no task matches '{task_id}'", err=True)
            raise SystemExit(1)
        try:
            cancel_task_record(row)
        except TaskControlError as exc:
            click.echo(
                str(exc),
                err=True,
            )
            raise SystemExit(2)
        session.commit()
        click.echo(f"cancelled {row.id}")
    finally:
        session.close()


@tasks_cmd.command("kinds")
def tasks_kinds() -> None:
    """List every task name a worker is willing to execute."""
    for name in KNOWN_TASK_NAMES:
        click.echo(name)


def _parse_since(value: str) -> datetime:
    now = datetime.now(UTC)
    if value.endswith("h"):
        return now - timedelta(hours=int(value[:-1]))
    if value.endswith("d"):
        return now - timedelta(days=int(value[:-1]))
    return datetime.fromisoformat(value)


def _row_to_dict(row: TaskRecord) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "celery_task_id": row.celery_task_id,
        "tenant_id": row.tenant_id,
        "kind": row.kind,
        "queue": row.queue,
        "status": row.status,
        "attempts": row.attempts,
        "payload": row.payload,
        "idempotency_key": row.idempotency_key,
        "parent_task_id": str(row.parent_task_id) if row.parent_task_id else None,
        "trace_id": row.trace_id,
        "last_error": row.last_error,
        "scheduled_for": row.scheduled_for.isoformat() if row.scheduled_for else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
