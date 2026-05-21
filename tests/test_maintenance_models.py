"""Phase 18.4: schema introspection for the cleanup audit tables and
the soft-delete column on ``applications``.

Schema tests stay local (no live DB) but pin the wire-level contract
so a future drift produces a fast, readable failure rather than a
runtime mystery.
"""

from __future__ import annotations

import importlib


def test_application_has_deleted_at_column() -> None:
    models = importlib.import_module("src.core.models")
    cols = models.Application.__table__.columns
    assert "deleted_at" in cols
    assert cols["deleted_at"].nullable is True


def test_cleanup_runs_columns_present() -> None:
    models = importlib.import_module("src.core.models")
    cols = set(models.CleanupRun.__table__.columns.keys())
    expected = {
        "id",
        "tenant_id",
        "mode",
        "trigger",
        "started_at",
        "finished_at",
        "scanned_count",
        "protected_count",
        "quarantined_count",
        "purged_count",
        "restored_count",
        "error_count",
        "bytes_reclaimed",
        "summary",
    }
    assert expected <= cols, f"missing: {expected - cols}"


def test_cleanup_items_columns_present() -> None:
    models = importlib.import_module("src.core.models")
    cols = set(models.CleanupItem.__table__.columns.keys())
    expected = {
        "id",
        "run_id",
        "tenant_id",
        "path",
        "quarantine_path",
        "category",
        "action",
        "size_bytes",
        "mtime",
        "quarantined_at",
        "reason",
        "created_at",
    }
    assert expected <= cols, f"missing: {expected - cols}"


def test_cleanup_items_has_cascade_fk_to_runs() -> None:
    models = importlib.import_module("src.core.models")
    fks = list(models.CleanupItem.__table__.foreign_keys)
    fk = next((fk for fk in fks if fk.column.table.name == "cleanup_runs"), None)
    assert fk is not None, "cleanup_items must FK into cleanup_runs"
    assert fk.ondelete == "CASCADE"


def test_cascade_quarantine_flushes_run_before_items() -> None:
    """Regression: cleanup_items has a FK to cleanup_runs but no ORM
    ``relationship`` declared, so the unit-of-work has no dependency
    edge between parent + child. Without an explicit ``session.flush()``
    after the CleanupRun is added, SQLAlchemy is free to bulk-insert
    the CleanupItem rows first, which trips the FK constraint and
    aborts a cascade=True DELETE on /api/applications.

    Pin the fix at the source level: between ``session.add(run)`` and
    the per-path loop body we MUST call ``session.flush()`` so the
    parent INSERT lands before any child INSERT.
    """
    import importlib

    src_path = importlib.import_module("src.application.tracking").__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as fh:
        body = fh.read()

    # Locate the cascade helper and inspect just its body so we only
    # assert about that function (otherwise an unrelated ``session.flush``
    # elsewhere in the module could mask a regression).
    fn_marker = "def _cascade_quarantine_application_artifacts("
    fn_start = body.find(fn_marker)
    assert fn_start != -1, "Cascade helper renamed; update this regression test."
    next_fn = body.find("\ndef ", fn_start + len(fn_marker))
    fn_body = body[fn_start : next_fn if next_fn != -1 else len(body)]

    add_run_idx = fn_body.find("session.add(run)")
    flush_idx = fn_body.find("session.flush()")
    for_loop_idx = fn_body.find("for raw in paths:")
    assert add_run_idx != -1, "session.add(run) missing -- cascade helper rewritten?"
    assert for_loop_idx != -1, "for raw in paths loop missing -- helper rewritten?"
    assert flush_idx != -1 and add_run_idx < flush_idx < for_loop_idx, (
        "Expected ``session.flush()`` to sit between ``session.add(run)`` "
        "and the ``for raw in paths`` loop in "
        "_cascade_quarantine_application_artifacts so the cleanup_runs row "
        "is inserted before any cleanup_items references it. Without this "
        "flush the cascade=True DELETE /api/applications path trips a "
        "fk_cleanup_items_run FK violation."
    )
