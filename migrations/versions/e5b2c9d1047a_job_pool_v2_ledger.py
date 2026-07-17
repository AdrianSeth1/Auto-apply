"""Job Pool V2 source, evaluation, portfolio, and feedback ledger.

Revision ID: e5b2c9d1047a
Revises: d4e8a2b7c901
Create Date: 2026-07-12

All changes are additive. V1 ignores these tables and nullable links.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5b2c9d1047a"
down_revision: str | Sequence[str] | None = "d4e8a2b7c901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()


def _tenant() -> sa.Column:
    return sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default")


def upgrade() -> None:
    op.create_table(
        "employers",
        sa.Column("id", UUID, nullable=False),
        _tenant(),
        sa.Column("normalized_name", sa.String(240), nullable=False),
        sa.Column("display_name", sa.String(240), nullable=False),
        sa.Column("aliases", JSONB, nullable=True),
        sa.Column("canonical_domain", sa.String(240), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "normalized_name", name="uq_employers_tenant_name"),
    )
    op.create_index("ix_employers_tenant_name", "employers", ["tenant_id", "normalized_name"])

    op.create_table(
        "employer_assessments",
        sa.Column("id", UUID, nullable=False),
        _tenant(),
        sa.Column("employer_id", UUID, nullable=False),
        sa.Column("classifier_version", sa.String(80), nullable=False),
        sa.Column("employment_relationship", sa.String(40), nullable=False),
        sa.Column("business_model", sa.String(60), nullable=False),
        sa.Column("lifecycle", sa.String(30), nullable=False),
        sa.Column("funding_stage", sa.String(30), nullable=False),
        sa.Column("selectivity_tier", sa.String(30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", JSONB, nullable=True),
        sa.Column("manual_override", JSONB, nullable=True),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["employer_id"], ["employers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "employer_id", "classifier_version", name="uq_employer_assessment_version"
        ),
    )
    op.create_index(
        "ix_employer_assessments_tenant_employer",
        "employer_assessments",
        ["tenant_id", "employer_id"],
    )

    op.create_table(
        "source_endpoints",
        sa.Column("id", UUID, nullable=False),
        _tenant(),
        sa.Column("adapter", sa.String(50), nullable=False),
        sa.Column("endpoint_key", sa.String(300), nullable=False),
        sa.Column("employer_id", UUID, nullable=True),
        sa.Column("careers_url", sa.Text(), nullable=True),
        sa.Column("adapter_config", JSONB, nullable=True),
        sa.Column("discovery_provenance", JSONB, nullable=True),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("compliance_status", sa.String(40), nullable=False),
        sa.Column("manual_override", JSONB, nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("consecutive_empty", sa.Integer(), nullable=False),
        sa.Column("recovery_successes", sa.Integer(), nullable=False),
        sa.Column("first_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_nonempty_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["employer_id"], ["employers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "adapter", "endpoint_key", name="uq_source_endpoints_adapter_key"
        ),
    )
    op.create_index(
        "ix_source_endpoints_due", "source_endpoints", ["tenant_id", "state", "next_probe_at"]
    )
    op.create_index(
        "ix_source_endpoints_employer", "source_endpoints", ["tenant_id", "employer_id"]
    )

    op.create_table(
        "discovery_runs",
        sa.Column("id", UUID, nullable=False),
        _tenant(),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("pipeline_version", sa.String(30), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("target_ids", JSONB, nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("counts", JSONB, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_discovery_runs_tenant_started", "discovery_runs", ["tenant_id", "started_at"]
    )
    op.create_index("ix_discovery_runs_status", "discovery_runs", ["tenant_id", "status"])

    op.create_table(
        "source_endpoint_runs",
        sa.Column("id", UUID, nullable=False),
        _tenant(),
        sa.Column("endpoint_id", UUID, nullable=False),
        sa.Column("discovery_run_id", UUID, nullable=True),
        sa.Column("fetch_run_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("provider_records", sa.Integer(), nullable=False),
        sa.Column("normalized_records", sa.Integer(), nullable=False),
        sa.Column("malformed_records", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("response_signature", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("retry_after_seconds", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["endpoint_id"], ["source_endpoints.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["discovery_run_id"], ["discovery_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "fetch_run_id", name="uq_source_endpoint_fetch_run"),
    )
    op.create_index(
        "ix_source_endpoint_runs_endpoint_started",
        "source_endpoint_runs",
        ["endpoint_id", "started_at"],
    )
    op.create_index(
        "ix_source_endpoint_runs_discovery", "source_endpoint_runs", ["discovery_run_id"]
    )

    op.create_table(
        "source_query_arms",
        sa.Column("id", UUID, nullable=False),
        _tenant(),
        sa.Column("target_id", sa.String(100), nullable=False),
        sa.Column("adapter", sa.String(50), nullable=False),
        sa.Column("query", sa.String(300), nullable=False),
        sa.Column("normalized_query", sa.String(300), nullable=False),
        sa.Column("geography", sa.String(160), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("call_cost", sa.Float(), nullable=False),
        sa.Column("run_count", sa.Integer(), nullable=False),
        sa.Column("useful_yield_positive", sa.Float(), nullable=False),
        sa.Column("useful_yield_total", sa.Float(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "target_id",
            "adapter",
            "normalized_query",
            "geography",
            "version",
            name="uq_source_query_arm_version",
        ),
    )
    op.create_index(
        "ix_source_query_arms_due", "source_query_arms", ["tenant_id", "state", "next_run_at"]
    )

    op.create_table(
        "source_query_runs",
        sa.Column("id", UUID, nullable=False),
        _tenant(),
        sa.Column("query_arm_id", UUID, nullable=False),
        sa.Column("discovery_run_id", UUID, nullable=True),
        sa.Column("search_query_id", UUID, nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("provider_records", sa.Integer(), nullable=False),
        sa.Column("unique_postings", sa.Integer(), nullable=False),
        sa.Column("routed_pairs", sa.Integer(), nullable=False),
        sa.Column("viable_evaluations", sa.Integer(), nullable=False),
        sa.Column("review_positives", sa.Integer(), nullable=False),
        sa.Column("applications", sa.Integer(), nullable=False),
        sa.Column("metrics", JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["query_arm_id"], ["source_query_arms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["discovery_run_id"], ["discovery_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["search_query_id"], ["search_queries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_source_query_runs_arm_started", "source_query_runs", ["query_arm_id", "started_at"]
    )
    op.create_index("ix_source_query_runs_discovery", "source_query_runs", ["discovery_run_id"])

    op.create_table(
        "job_quality_assessments",
        sa.Column("id", UUID, nullable=False),
        _tenant(),
        sa.Column("snapshot_id", UUID, nullable=False),
        sa.Column("classifier_version", sa.String(80), nullable=False),
        sa.Column("assessment", JSONB, nullable=False),
        sa.Column("trust_score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["job_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "snapshot_id", "classifier_version", name="uq_job_quality_snapshot_version"
        ),
    )
    op.create_index(
        "ix_job_quality_snapshot", "job_quality_assessments", ["tenant_id", "snapshot_id"]
    )

    op.create_table(
        "job_target_evaluations",
        sa.Column("id", UUID, nullable=False),
        _tenant(),
        sa.Column("discovery_run_id", UUID, nullable=True),
        sa.Column("snapshot_id", UUID, nullable=False),
        sa.Column("target_id", sa.String(100), nullable=False),
        sa.Column("candidate_version", sa.String(64), nullable=False),
        sa.Column("target_version", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(80), nullable=False),
        sa.Column("role_taxonomy_version", sa.String(64), nullable=False),
        sa.Column("capability_taxonomy_version", sa.String(64), nullable=False),
        sa.Column("scorer_version", sa.String(80), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("pipeline_version", sa.String(30), nullable=False),
        sa.Column("stage_status", sa.String(30), nullable=False),
        sa.Column("facts", JSONB, nullable=False),
        sa.Column("gate_results", JSONB, nullable=False),
        sa.Column("component_scores", JSONB, nullable=False),
        sa.Column("component_confidence", JSONB, nullable=False),
        sa.Column("story_fit", sa.Float(), nullable=False),
        sa.Column("candidacy_index", sa.Float(), nullable=False),
        sa.Column("review_index", sa.Float(), nullable=False),
        sa.Column("adjusted_review_index", sa.Float(), nullable=False),
        sa.Column("tier", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("explanation", JSONB, nullable=False),
        sa.Column("employer_assessment", JSONB, nullable=False),
        sa.Column("posting_assessment", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["discovery_run_id"], ["discovery_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["job_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "snapshot_id",
            "target_id",
            "candidate_version",
            "target_version",
            "parser_version",
            "role_taxonomy_version",
            "capability_taxonomy_version",
            "scorer_version",
            name="uq_job_target_evaluation_version",
        ),
    )
    op.create_index(
        "ix_job_target_eval_run_target",
        "job_target_evaluations",
        ["discovery_run_id", "target_id"],
    )
    op.create_index(
        "ix_job_target_eval_tier", "job_target_evaluations", ["tenant_id", "target_id", "tier"]
    )
    op.create_index(
        "ix_job_target_eval_snapshot", "job_target_evaluations", ["tenant_id", "snapshot_id"]
    )

    op.create_table(
        "job_evaluation_reasons",
        sa.Column("id", UUID, nullable=False),
        _tenant(),
        sa.Column("evaluation_id", UUID, nullable=False),
        sa.Column("discovery_run_id", UUID, nullable=True),
        sa.Column("target_id", sa.String(100), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("evidence", JSONB, nullable=True),
        sa.Column("details", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["evaluation_id"], ["job_target_evaluations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["discovery_run_id"], ["discovery_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_job_eval_reasons_run_target",
        "job_evaluation_reasons",
        ["discovery_run_id", "target_id"],
    )
    op.create_index(
        "ix_job_eval_reasons_code",
        "job_evaluation_reasons",
        ["tenant_id", "stage", "reason_code"],
    )
    op.create_index(
        "ix_job_eval_reasons_evaluation", "job_evaluation_reasons", ["evaluation_id"]
    )

    op.create_table(
        "portfolio_runs",
        sa.Column("id", UUID, nullable=False),
        _tenant(),
        sa.Column("discovery_run_id", UUID, nullable=True),
        sa.Column("portfolio_id", sa.String(100), nullable=False),
        sa.Column("portfolio_version", sa.String(64), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("seed", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("counts", JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["discovery_run_id"], ["discovery_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_portfolio_runs_tenant_started", "portfolio_runs", ["tenant_id", "started_at"]
    )
    op.create_index("ix_portfolio_runs_discovery", "portfolio_runs", ["discovery_run_id"])

    op.create_table(
        "portfolio_decisions",
        sa.Column("id", UUID, nullable=False),
        _tenant(),
        sa.Column("portfolio_run_id", UUID, nullable=False),
        sa.Column("evaluation_id", UUID, nullable=False),
        sa.Column("canonical_group", sa.String(100), nullable=False),
        sa.Column("owned_target_id", sa.String(100), nullable=False),
        sa.Column("secondary_target_ids", JSONB, nullable=True),
        sa.Column("company_key", sa.String(240), nullable=False),
        sa.Column("lane", sa.String(30), nullable=False),
        sa.Column("utility", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("reason_codes", JSONB, nullable=True),
        sa.Column("review_id", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_run_id"], ["portfolio_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evaluation_id"], ["job_target_evaluations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("portfolio_run_id", "evaluation_id", name="uq_portfolio_decision_eval"),
    )
    op.create_index(
        "ix_portfolio_decisions_run_lane",
        "portfolio_decisions",
        ["portfolio_run_id", "lane", "selected"],
    )
    op.create_index(
        "ix_portfolio_decisions_canonical",
        "portfolio_decisions",
        ["portfolio_run_id", "canonical_group"],
    )
    op.create_index("ix_portfolio_decisions_review_id", "portfolio_decisions", ["review_id"])

    op.create_table(
        "review_feedback",
        sa.Column("id", UUID, nullable=False),
        _tenant(),
        sa.Column("review_id", UUID, nullable=True),
        sa.Column("evaluation_id", UUID, nullable=False),
        sa.Column("target_id", sa.String(100), nullable=False),
        sa.Column("judgment", sa.String(40), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("primary_reason", sa.String(100), nullable=False),
        sa.Column("secondary_reasons", JSONB, nullable=True),
        sa.Column("free_text", sa.Text(), nullable=True),
        sa.Column("learnable", sa.Boolean(), nullable=False),
        sa.Column("model_version", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["evaluation_id"], ["job_target_evaluations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_review_feedback_target_created",
        "review_feedback",
        ["tenant_id", "target_id", "created_at"],
    )
    op.create_index("ix_review_feedback_evaluation", "review_feedback", ["evaluation_id"])
    op.create_index("ix_review_feedback_review_id", "review_feedback", ["review_id"])

    op.create_table(
        "evaluation_sets",
        sa.Column("id", UUID, nullable=False),
        _tenant(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("definition", JSONB, nullable=False),
        sa.Column("seed", sa.String(100), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", "version", name="uq_evaluation_set_version"),
    )
    op.create_index(
        "ix_evaluation_sets_tenant_created", "evaluation_sets", ["tenant_id", "created_at"]
    )

    op.create_table(
        "evaluation_items",
        sa.Column("id", UUID, nullable=False),
        _tenant(),
        sa.Column("evaluation_set_id", UUID, nullable=False),
        sa.Column("snapshot_id", UUID, nullable=False),
        sa.Column("target_id", sa.String(100), nullable=False),
        sa.Column("evaluation_id", UUID, nullable=True),
        sa.Column("hidden_arm", sa.String(30), nullable=False),
        sa.Column("presentation_order", sa.Integer(), nullable=False),
        sa.Column("canonical_group", sa.String(100), nullable=True),
        sa.Column("judgment", sa.String(40), nullable=True),
        sa.Column("primary_reason", sa.String(100), nullable=True),
        sa.Column("secondary_reasons", JSONB, nullable=True),
        sa.Column("judged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["evaluation_set_id"], ["evaluation_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["job_snapshots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evaluation_id"], ["job_target_evaluations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evaluation_set_id", "snapshot_id", "target_id", name="uq_evaluation_item_pair"),
    )
    op.create_index(
        "ix_evaluation_items_order", "evaluation_items", ["evaluation_set_id", "presentation_order"]
    )

    op.add_column("applications", sa.Column("evaluation_id", UUID, nullable=True))
    op.create_index("ix_applications_evaluation_id", "applications", ["evaluation_id"])
    op.create_foreign_key(
        "fk_applications_evaluation",
        "applications",
        "job_target_evaluations",
        ["evaluation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("funnel_events", sa.Column("evaluation_id", UUID, nullable=True))
    op.add_column("funnel_events", sa.Column("journey_key", sa.String(120), nullable=True))
    op.create_index("ix_funnel_events_evaluation_id", "funnel_events", ["evaluation_id"])
    op.create_index("ix_funnel_events_journey_key", "funnel_events", ["journey_key"])
    op.create_foreign_key(
        "fk_funnel_events_evaluation",
        "funnel_events",
        "job_target_evaluations",
        ["evaluation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("job_postings", sa.Column("source_endpoint_id", UUID, nullable=True))
    op.add_column("job_postings", sa.Column("employer_id", UUID, nullable=True))
    op.create_index("ix_job_postings_source_endpoint_id", "job_postings", ["source_endpoint_id"])
    op.create_index("ix_job_postings_employer_id", "job_postings", ["employer_id"])
    op.create_foreign_key(
        "fk_job_postings_source_endpoint",
        "job_postings",
        "source_endpoints",
        ["source_endpoint_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_job_postings_employer",
        "job_postings",
        "employers",
        ["employer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("job_snapshots", sa.Column("provenance", JSONB, nullable=True))
    op.add_column("job_snapshots", sa.Column("source_endpoint_run_id", UUID, nullable=True))
    op.add_column("job_snapshots", sa.Column("source_query_run_id", UUID, nullable=True))
    op.create_index(
        "ix_job_snapshots_source_endpoint_run_id", "job_snapshots", ["source_endpoint_run_id"]
    )
    op.create_index(
        "ix_job_snapshots_source_query_run_id", "job_snapshots", ["source_query_run_id"]
    )
    op.create_foreign_key(
        "fk_job_snapshots_endpoint_run",
        "job_snapshots",
        "source_endpoint_runs",
        ["source_endpoint_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_job_snapshots_query_run",
        "job_snapshots",
        "source_query_runs",
        ["source_query_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("review_queue", sa.Column("evaluation_id", UUID, nullable=True))
    op.add_column("review_queue", sa.Column("portfolio_decision_id", UUID, nullable=True))
    op.create_index("ix_review_queue_evaluation_id", "review_queue", ["evaluation_id"])
    op.create_index(
        "ix_review_queue_portfolio_decision_id", "review_queue", ["portfolio_decision_id"]
    )
    op.create_foreign_key(
        "fk_review_queue_evaluation",
        "review_queue",
        "job_target_evaluations",
        ["evaluation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_review_queue_portfolio_decision",
        "review_queue",
        "portfolio_decisions",
        ["portfolio_decision_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    for table, constraint in (
        ("review_queue", "fk_review_queue_portfolio_decision"),
        ("review_queue", "fk_review_queue_evaluation"),
        ("job_snapshots", "fk_job_snapshots_query_run"),
        ("job_snapshots", "fk_job_snapshots_endpoint_run"),
        ("job_postings", "fk_job_postings_employer"),
        ("job_postings", "fk_job_postings_source_endpoint"),
        ("funnel_events", "fk_funnel_events_evaluation"),
        ("applications", "fk_applications_evaluation"),
    ):
        op.drop_constraint(constraint, table, type_="foreignkey")

    for table, index in (
        ("review_queue", "ix_review_queue_portfolio_decision_id"),
        ("review_queue", "ix_review_queue_evaluation_id"),
        ("job_snapshots", "ix_job_snapshots_source_query_run_id"),
        ("job_snapshots", "ix_job_snapshots_source_endpoint_run_id"),
        ("job_postings", "ix_job_postings_employer_id"),
        ("job_postings", "ix_job_postings_source_endpoint_id"),
        ("funnel_events", "ix_funnel_events_journey_key"),
        ("funnel_events", "ix_funnel_events_evaluation_id"),
        ("applications", "ix_applications_evaluation_id"),
    ):
        op.drop_index(index, table_name=table)

    op.drop_column("review_queue", "portfolio_decision_id")
    op.drop_column("review_queue", "evaluation_id")
    op.drop_column("job_snapshots", "source_query_run_id")
    op.drop_column("job_snapshots", "source_endpoint_run_id")
    op.drop_column("job_snapshots", "provenance")
    op.drop_column("job_postings", "employer_id")
    op.drop_column("job_postings", "source_endpoint_id")
    op.drop_column("funnel_events", "journey_key")
    op.drop_column("funnel_events", "evaluation_id")
    op.drop_column("applications", "evaluation_id")

    for table in (
        "evaluation_items",
        "evaluation_sets",
        "review_feedback",
        "portfolio_decisions",
        "portfolio_runs",
        "job_evaluation_reasons",
        "job_target_evaluations",
        "job_quality_assessments",
        "source_query_runs",
        "source_query_arms",
        "source_endpoint_runs",
        "discovery_runs",
        "source_endpoints",
        "employer_assessments",
        "employers",
    ):
        op.drop_table(table)
