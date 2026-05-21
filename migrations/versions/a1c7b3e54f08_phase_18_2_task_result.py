"""Phase 18.2: TaskRecord.result column for async task return values.

Adds a single nullable JSONB column on ``tasks``. The Phase 14.2
postrun signal handler now persists the worker's return value here so
``GET /api/tasks/{id}`` can surface produced artifacts / ids / errors
to async API callers without bouncing through Celery's transient
result backend.

Revision ID: a1c7b3e54f08
Revises: f4e8c1d2a907
Create Date: 2026-05-20 00:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1c7b3e54f08"
down_revision: str | Sequence[str] | None = "f4e8c1d2a907"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "result")
