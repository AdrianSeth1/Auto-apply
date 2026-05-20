"""Phase 18.4: cleanup audit tables + Application.deleted_at.

Adds three database objects backing the artifact-cleanup pipeline that
shipped in Phase 18.4:

* ``cleanup_runs`` -- one row per ``autoapply cleanup`` invocation or
  scheduled ``maintenance.cache_eviction`` tick. Stores counts +
  bytes reclaimed so the operator can see what a given run did
  without grepping logs.
* ``cleanup_items`` -- per-path detail rows joined to ``cleanup_runs``
  via ``run_id``. ``category`` is the classifier verdict and
  ``action`` is what cleanup decided to do.
* ``applications.deleted_at`` -- the soft-delete marker
  ``DELETE /api/applications/{id}`` writes. The cleanup task purges
  artifacts owned by rows older than
  ``cleanup.soft_deleted_retention_days``.

Per D026, ``tenant_id`` is required on every new audit table.

Revision ID: f4e8c1d2a907
Revises: e7c3a5b91f48
Create Date: 2026-05-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4e8c1d2a907"
down_revision: str | Sequence[str] | None = "e7c3a5b91f48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_DEFAULT = sa.text("'default'")


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_applications_deleted_at",
        "applications",
        ["deleted_at"],
        unique=False,
    )

    op.create_table(
        "cleanup_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            nullable=False,
            server_default=_TENANT_DEFAULT,
        ),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column(
            "trigger",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "scanned_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "protected_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "quarantined_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "purged_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "restored_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "error_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "bytes_reclaimed", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_cleanup_runs"),
        sa.CheckConstraint(
            "mode IN ('scan', 'clean', 'purge_quarantine', 'restore')",
            name="ck_cleanup_runs_mode",
        ),
        sa.CheckConstraint(
            "trigger IN ('scheduled', 'manual', 'api')",
            name="ck_cleanup_runs_trigger",
        ),
    )
    op.create_index(
        "ix_cleanup_runs_tenant_started",
        "cleanup_runs",
        ["tenant_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_cleanup_runs_mode", "cleanup_runs", ["mode"], unique=False
    )

    op.create_table(
        "cleanup_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            nullable=False,
            server_default=_TENANT_DEFAULT,
        ),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("quarantine_path", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("mtime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cleanup_items"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["cleanup_runs.id"],
            name="fk_cleanup_items_run",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_cleanup_items_run", "cleanup_items", ["run_id"], unique=False
    )
    op.create_index(
        "ix_cleanup_items_action_category",
        "cleanup_items",
        ["action", "category"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_cleanup_items_action_category", table_name="cleanup_items")
    op.drop_index("ix_cleanup_items_run", table_name="cleanup_items")
    op.drop_table("cleanup_items")
    op.drop_index("ix_cleanup_runs_mode", table_name="cleanup_runs")
    op.drop_index("ix_cleanup_runs_tenant_started", table_name="cleanup_runs")
    op.drop_table("cleanup_runs")
    op.drop_index("ix_applications_deleted_at", table_name="applications")
    op.drop_column("applications", "deleted_at")
