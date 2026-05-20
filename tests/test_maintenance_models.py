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
