"""Link immutable evaluations to every discovery run that reused them.

Revision ID: f6c1a7e2039b
Revises: e5b2c9d1047a
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6c1a7e2039b"
down_revision: str | Sequence[str] | None = "e5b2c9d1047a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid_type = postgresql.UUID(as_uuid=True)
    op.create_table(
        "discovery_run_evaluations",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("discovery_run_id", uuid_type, nullable=False),
        sa.Column("evaluation_id", uuid_type, nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["discovery_run_id"], ["discovery_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evaluation_id"], ["job_target_evaluations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("discovery_run_id", "evaluation_id", name="uq_discovery_run_evaluation"),
    )
    op.create_index("ix_discovery_run_evaluations_run", "discovery_run_evaluations", ["discovery_run_id"])
    op.create_index("ix_discovery_run_evaluations_evaluation", "discovery_run_evaluations", ["evaluation_id"])
    op.execute(
        """
        INSERT INTO discovery_run_evaluations (id, tenant_id, discovery_run_id, evaluation_id, linked_at)
        SELECT gen_random_uuid(), tenant_id, discovery_run_id, id, created_at
        FROM job_target_evaluations
        WHERE discovery_run_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_discovery_run_evaluations_evaluation", table_name="discovery_run_evaluations")
    op.drop_index("ix_discovery_run_evaluations_run", table_name="discovery_run_evaluations")
    op.drop_table("discovery_run_evaluations")
