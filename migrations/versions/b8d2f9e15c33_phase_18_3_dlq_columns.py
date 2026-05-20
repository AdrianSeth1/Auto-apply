"""Phase 18.3: dead-letter queue columns on ``tasks``.

Adds three nullable columns + a partial index so the operator UI can
list DLQ rows fast:

* ``last_attempted_at`` -- updated on every prerun signal so the row
  knows when it most recently went into ``running``.
* ``dead_lettered_at`` -- set when ``max_retries`` is exhausted; the
  task status flips to ``dead_lettered`` so DLQ rows don't compete
  with normal ``failed`` rows in the kanban.
* ``dlq_reason`` -- short string summary captured at DLQ time.

Revision ID: b8d2f9e15c33
Revises: a1c7b3e54f08
Create Date: 2026-05-20 01:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d2f9e15c33"
down_revision: str | Sequence[str] | None = "a1c7b3e54f08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("dlq_reason", sa.Text(), nullable=True),
    )
    # Partial index for the "Stuck / failed" tab the SPA renders --
    # filtering on a small subset of statuses is the common read.
    op.create_index(
        "ix_tasks_dlq",
        "tasks",
        ["tenant_id", "dead_lettered_at"],
        unique=False,
        postgresql_where=sa.text("status = 'dead_lettered'"),
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_dlq", table_name="tasks")
    op.drop_column("tasks", "dlq_reason")
    op.drop_column("tasks", "dead_lettered_at")
    op.drop_column("tasks", "last_attempted_at")
