"""Phase 18.3: tests for the DLQ + manual retry plumbing.

End-to-end "kill a worker mid-task" tests require a real Celery
worker subprocess + broker; that part of the plan is tracked
separately because it needs Redis. The unit tests here cover the
state-machine surface the operator UI depends on:

* Schema: ``dead_lettered_at`` / ``dlq_reason`` / ``last_attempted_at``
  columns exist and the partial DLQ index is registered.
* ``retry_task_record`` accepts ``dead_lettered`` as a valid source
  state; ``discard_task_record`` is the no-retry escape hatch.
* ``task_failure_handler`` flips a row past ``max_retries`` to
  ``dead_lettered`` instead of plain ``failed``.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.application.task_control import (
    TaskControlError,
    discard_task_record,
    retry_task_record,
)
from src.core.models import TaskRecord
from src.tasks import audit


def test_task_record_has_dlq_columns() -> None:
    models = importlib.import_module("src.core.models")
    cols = models.TaskRecord.__table__.columns
    assert "last_attempted_at" in cols
    assert "dead_lettered_at" in cols
    assert "dlq_reason" in cols


def test_tasks_table_has_partial_dlq_index() -> None:
    models = importlib.import_module("src.core.models")
    index_names = {idx.name for idx in models.TaskRecord.__table__.indexes}
    assert "ix_tasks_dlq" in index_names


def _make_row(status: str = "failed") -> TaskRecord:
    return TaskRecord(
        id=uuid.uuid4(),
        tenant_id="default",
        kind="materials.generate",
        queue="materials",
        status=status,
        attempts=3,
    )


@patch("src.application.task_control.celery_app.send_task")
def test_retry_accepts_dead_lettered(mock_send: MagicMock) -> None:
    mock_send.return_value = SimpleNamespace(id="celery-1")
    row = _make_row("dead_lettered")
    result = retry_task_record(row)
    assert result["retried"] == str(row.id)
    assert result["kind"] == "materials.generate"
    mock_send.assert_called_once()


def test_retry_rejects_queued() -> None:
    row = _make_row("queued")
    with pytest.raises(TaskControlError):
        retry_task_record(row)


def test_discard_dead_lettered_transitions_to_cancelled() -> None:
    row = _make_row("dead_lettered")
    discard_task_record(row)
    assert row.status == "cancelled"


def test_discard_rejects_succeeded() -> None:
    row = _make_row("succeeded")
    with pytest.raises(TaskControlError):
        discard_task_record(row)


# ---- Signal-handler behaviour ----------------------------------------


def test_task_failure_handler_flips_to_dead_lettered_when_max_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 18.3: ``task_failure_handler`` looks at the Task object's
    ``max_retries`` and the row's ``attempts`` count. When attempts >
    max_retries the row goes to ``dead_lettered`` with
    ``dead_lettered_at`` + ``dlq_reason`` populated."""
    row = TaskRecord(
        id=uuid.uuid4(),
        tenant_id="default",
        kind="materials.generate",
        queue="materials",
        status="running",
        attempts=4,
    )

    captured: dict[str, object] = {}

    def fake_safe_update(task_id, mutate):
        mutate(row)
        captured["called"] = True

    monkeypatch.setattr(audit, "_safe_update", fake_safe_update)

    sender = SimpleNamespace(max_retries=3)
    audit.task_failure_handler(
        task_id="celery-1",
        exception=RuntimeError("boom"),
        sender=sender,
    )

    assert captured.get("called") is True
    assert row.status == "dead_lettered"
    assert row.dead_lettered_at is not None
    assert row.dlq_reason and "boom" in row.dlq_reason


def test_task_failure_handler_keeps_failed_when_retries_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = TaskRecord(
        id=uuid.uuid4(),
        tenant_id="default",
        kind="materials.generate",
        queue="materials",
        status="running",
        attempts=2,
    )

    def fake_safe_update(_task_id, mutate):
        mutate(row)

    monkeypatch.setattr(audit, "_safe_update", fake_safe_update)

    sender = SimpleNamespace(max_retries=3)
    audit.task_failure_handler(
        task_id="celery-1",
        exception=RuntimeError("transient"),
        sender=sender,
    )

    assert row.status == "failed"
    assert row.dead_lettered_at is None


def test_task_prerun_handler_records_last_attempted_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = TaskRecord(
        id=uuid.uuid4(),
        tenant_id="default",
        kind="materials.generate",
        queue="materials",
        status="queued",
        attempts=0,
    )

    def fake_safe_update(_task_id, mutate):
        mutate(row)

    monkeypatch.setattr(audit, "_safe_update", fake_safe_update)

    before = datetime.now(UTC)
    audit.task_prerun_handler(task_id="celery-1")
    after = datetime.now(UTC)

    assert row.status == "running"
    assert row.attempts == 1
    assert row.last_attempted_at is not None
    assert before <= row.last_attempted_at <= after
