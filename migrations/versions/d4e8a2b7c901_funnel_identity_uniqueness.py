"""Funnel events, conservative job identity, and legacy source uniqueness.

Revision ID: d4e8a2b7c901
Revises: c3a7e1f2b048
Create Date: 2026-07-11

The legacy ``jobs`` table historically deduplicated by source + company +
source_id in Python. Source IDs are source-global, so company was both
unnecessary and dangerous when names changed. This migration selects the
oldest row as keeper, repoints durable application/review references, removes
the redundant rows, and installs a normalized partial unique index.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e8a2b7c901"
down_revision: str | Sequence[str] | None = "c3a7e1f2b048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("canonical_fingerprint", sa.String(24), nullable=True))
    op.create_index("ix_jobs_canonical_fingerprint", "jobs", ["canonical_fingerprint"])
    op.add_column(
        "job_postings", sa.Column("canonical_fingerprint", sa.String(24), nullable=True)
    )
    op.create_index(
        "ix_job_postings_canonical_fingerprint", "job_postings", ["canonical_fingerprint"]
    )
    op.add_column("applications", sa.Column("profile_variant", sa.String(100), nullable=True))
    op.add_column("applications", sa.Column("material_variant", sa.String(1000), nullable=True))
    op.add_column("applications", sa.Column("time_spent_seconds", sa.Integer(), nullable=True))

    op.create_table(
        "funnel_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("posting_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("profile_variant", sa.String(100), nullable=True),
        sa.Column("material_variant", sa.String(1000), nullable=True),
        sa.Column("time_spent_seconds", sa.Integer(), nullable=True),
        sa.Column("event_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "entity_type", "entity_id", "stage", name="uq_funnel_event_stage"
        ),
    )
    op.create_index("ix_funnel_events_tenant_time", "funnel_events", ["tenant_id", "occurred_at"])
    op.create_index(
        "ix_funnel_events_stage_time", "funnel_events", ["tenant_id", "stage", "occurred_at"]
    )
    op.create_index("ix_funnel_events_job_id", "funnel_events", ["job_id"])
    op.create_index("ix_funnel_events_posting_id", "funnel_events", ["posting_id"])
    op.create_index("ix_funnel_events_application_id", "funnel_events", ["application_id"])

    # Backfill explicit variants already present in legacy paths/raw metadata.
    op.execute("""
        UPDATE applications a
        SET profile_variant = COALESCE(j.raw_data->>'best_profile', 'unknown'),
            material_variant = concat_ws('|', nullif(a.resume_version, ''), nullif(a.cover_letter_version, ''))
        FROM jobs j
        WHERE j.id = a.job_id
          AND (a.profile_variant IS NULL OR a.material_variant IS NULL)
    """)

    # Repoint duplicate legacy rows before deleting them. The index key is
    # intentionally identical to the normalized expression installed below.
    op.execute("""
        CREATE TEMP TABLE legacy_job_duplicate_map ON COMMIT DROP AS
        SELECT id AS duplicate_id,
               first_value(id) OVER (
                   PARTITION BY tenant_id, lower(btrim(source)), btrim(source_id)
                   ORDER BY discovered_at NULLS LAST, id
               ) AS keeper_id,
               row_number() OVER (
                   PARTITION BY tenant_id, lower(btrim(source)), btrim(source_id)
                   ORDER BY discovered_at NULLS LAST, id
               ) AS duplicate_rank
        FROM jobs
        WHERE source IS NOT NULL AND btrim(source) <> ''
          AND source_id IS NOT NULL AND btrim(source_id) <> ''
    """)
    op.execute("""
        UPDATE applications a SET job_id = m.keeper_id
        FROM legacy_job_duplicate_map m
        WHERE m.duplicate_rank > 1 AND a.job_id = m.duplicate_id
    """)
    op.execute("""
        UPDATE review_queue r SET job_id = m.keeper_id
        FROM legacy_job_duplicate_map m
        WHERE m.duplicate_rank > 1 AND r.job_id = m.duplicate_id
    """)
    op.execute("""
        DELETE FROM jobs j USING legacy_job_duplicate_map m
        WHERE m.duplicate_rank > 1 AND j.id = m.duplicate_id
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_jobs_tenant_source_source_id_normalized
        ON jobs (tenant_id, lower(btrim(source)), btrim(source_id))
        WHERE source IS NOT NULL AND btrim(source) <> ''
          AND source_id IS NOT NULL AND btrim(source_id) <> ''
    """)


def downgrade() -> None:
    op.drop_index("uq_jobs_tenant_source_source_id_normalized", table_name="jobs")
    op.drop_table("funnel_events")
    op.drop_column("applications", "time_spent_seconds")
    op.drop_column("applications", "material_variant")
    op.drop_column("applications", "profile_variant")
    op.drop_index("ix_job_postings_canonical_fingerprint", table_name="job_postings")
    op.drop_column("job_postings", "canonical_fingerprint")
    op.drop_index("ix_jobs_canonical_fingerprint", table_name="jobs")
    op.drop_column("jobs", "canonical_fingerprint")
