"""Phase 18.2: tests for the async task-result contract.

We exercise:

* The :data:`TaskRecord.result` column exists and is JSONB-shaped.
* The postrun signal helper coerces arbitrary return values to a
  storage-safe dict.
* The materials.generate task body still composes a structured
  return shape that includes the artifact paths the SPA polls for.
"""

from __future__ import annotations

import importlib


def test_task_record_has_result_column() -> None:
    models = importlib.import_module("src.core.models")
    cols = models.TaskRecord.__table__.columns
    assert "result" in cols
    assert cols["result"].nullable is True


def test_coerce_result_for_storage_passes_dict_through() -> None:
    from src.tasks.audit import _coerce_result_for_storage

    out = _coerce_result_for_storage(
        {
            "task": "materials.generate",
            "artifacts": {"resume": {"path": "/data/output/r.docx"}},
        }
    )
    assert out["task"] == "materials.generate"
    assert out["artifacts"]["resume"]["path"] == "/data/output/r.docx"


def test_coerce_result_for_storage_wraps_non_dict() -> None:
    from src.tasks.audit import _coerce_result_for_storage

    assert _coerce_result_for_storage(42) == {"value": 42}
    assert _coerce_result_for_storage("hello") == {"value": "hello"}


def test_coerce_result_for_storage_handles_none() -> None:
    from src.tasks.audit import _coerce_result_for_storage

    assert _coerce_result_for_storage(None) is None


def test_coerce_result_for_storage_stringifies_non_json_safe() -> None:
    from datetime import UTC, datetime
    from pathlib import Path
    from uuid import UUID

    from src.tasks.audit import _coerce_result_for_storage

    out = _coerce_result_for_storage(
        {
            "now": datetime(2026, 5, 20, tzinfo=UTC),
            "path": Path("data/output/resume.docx"),
            "id": UUID("00000000-0000-0000-0000-000000000123"),
        }
    )
    assert isinstance(out, dict)
    # datetime got stringified via default=str during the json round-trip.
    assert "now" in out
    assert "2026-05-20" in str(out["now"])
    assert out["path"] == "data\\output\\resume.docx" or out["path"] == "data/output/resume.docx"
    assert out["id"] == "00000000-0000-0000-0000-000000000123"


def test_materials_generate_payload_accepts_inline_job() -> None:
    """Phase 18.2: the inline ``job`` payload field lets the JobsView
    enqueue without persisting first. The existing job_id path keeps
    working (lookup-by-UUID) when no inline job is supplied."""
    from src.tasks.tasks import MaterialsGeneratePayload

    inline = MaterialsGeneratePayload(
        job_id="abc",
        job={"company": "ACME", "title": "Engineer"},
    )
    assert inline.job == {"company": "ACME", "title": "Engineer"}
    persisted = MaterialsGeneratePayload(job_id="00000000-0000-0000-0000-000000000001")
    assert persisted.job is None


def test_materials_generate_payload_accepts_application_id() -> None:
    """Phase 18.2: the regenerate flow names the Application so the
    task body can write artifact paths back onto that row."""
    from src.tasks.tasks import MaterialsGeneratePayload

    payload = MaterialsGeneratePayload(
        job_id="abc",
        application_id="00000000-0000-0000-0000-000000000099",
    )
    assert payload.application_id == "00000000-0000-0000-0000-000000000099"
