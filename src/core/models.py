"""SQLAlchemy ORM models for all database tables.

Covers: jobs, applications, applicant_profile, bullet_pool, qa_bank.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_uuid() -> uuid.UUID:
    return uuid.uuid4()


TENANT_DEFAULT = "default"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    source: Mapped[str | None] = mapped_column(String(50))
    source_id: Mapped[str | None] = mapped_column(String(200), index=True)
    company: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    location: Mapped[str | None] = mapped_column(String(200))
    employment_type: Mapped[str | None] = mapped_column(String(50))
    seniority: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    description_embedding = mapped_column(Vector(1536), nullable=True)
    requirements: Mapped[dict | None] = mapped_column(JSONB)
    visa_sponsorship: Mapped[bool | None] = mapped_column(Boolean)
    ats_type: Mapped[str | None] = mapped_column(String(50))
    application_url: Mapped[str | None] = mapped_column(Text)
    canonical_fingerprint: Mapped[str | None] = mapped_column(String(24), index=True)
    raw_data: Mapped[dict | None] = mapped_column(JSONB)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", name="fk_applications_job_id"),
        nullable=False,
        index=True,
    )
    job_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_snapshots.id", name="fk_applications_job_snapshot"),
        nullable=True,
        index=True,
    )
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="DISCOVERED")
    match_score: Mapped[float | None] = mapped_column(Float)
    resume_version: Mapped[str | None] = mapped_column(Text)
    cover_letter_version: Mapped[str | None] = mapped_column(Text)
    profile_variant: Mapped[str | None] = mapped_column(String(100))
    material_variant: Mapped[str | None] = mapped_column(String(1000))
    time_spent_seconds: Mapped[int | None] = mapped_column(Integer)
    qa_responses: Mapped[dict | None] = mapped_column(JSONB)
    screenshot_paths: Mapped[dict | None] = mapped_column(JSONB)
    error_log: Mapped[str | None] = mapped_column(Text)
    state_history: Mapped[list | None] = mapped_column(JSONB)  # list[dict] FSM audit trail
    fields_filled: Mapped[int | None] = mapped_column(Integer)
    fields_total: Mapped[int | None] = mapped_column(Integer)
    # Phase 18.5: per-field record persisted from the form-filler so the
    # operator can expand the "N of M fields filled" badge in the
    # Review queue and see exactly which fields were detected, what we
    # tried to fill, whether it succeeded, and why the misses missed.
    # See FieldMapping in src/execution/form_filler.py for the shape.
    fill_details: Mapped[list | None] = mapped_column(JSONB)
    files_uploaded: Mapped[list | None] = mapped_column(JSONB)  # list[str] uploaded filenames
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(30))  # pending/rejected/oa/interview/offer
    outcome_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Phase 18.4: soft-delete column for the cascade artifact-cleanup
    # API. ``DELETE /api/applications/{id}`` flips this; permanent
    # deletion happens after ``cleanup.soft_deleted_retention_days``.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FunnelEvent(Base):
    """Append-only, idempotent milestones for weekly conversion analytics."""

    __tablename__ = "funnel_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "entity_type", "entity_id", "stage", name="uq_funnel_event_stage"
        ),
        Index("ix_funnel_events_tenant_time", "tenant_id", "occurred_at"),
        Index("ix_funnel_events_stage_time", "tenant_id", "stage", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    stage: Mapped[str] = mapped_column(String(30), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    posting_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    application_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    journey_key: Mapped[str | None] = mapped_column(String(120), index=True)
    source: Mapped[str | None] = mapped_column(String(50))
    profile_variant: Mapped[str | None] = mapped_column(String(100))
    material_variant: Mapped[str | None] = mapped_column(String(1000))
    time_spent_seconds: Mapped[int | None] = mapped_column(Integer)
    event_metadata: Mapped[dict | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ApplicantProfile(Base):
    __tablename__ = "applicant_profile"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    section: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_embedding = mapped_column(Vector(1536), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class BulletPool(Base):
    __tablename__ = "bullet_pool"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    category: Mapped[str | None] = mapped_column(String(50))
    source_entity: Mapped[str | None] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_embedding = mapped_column(Vector(1536), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    used_count: Mapped[int] = mapped_column(Integer, default=0)


TENANT_DEFAULT = "default"


class JobPosting(Base):
    """Stable job entity per (tenant, source, source_id). Phase 13."""

    __tablename__ = "job_postings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source", "source_id", name="uq_job_postings_tenant_source"),
        Index("ix_job_postings_tenant_state", "tenant_id", "state"),
        Index("ix_job_postings_company", "tenant_id", "company"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str] = mapped_column(String(200), nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    canonical_fingerprint: Mapped[str | None] = mapped_column(String(24), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    latest_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_endpoint_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    employer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)


class JobSnapshot(Base):
    """Immutable content-versioned snapshot of a JobPosting. Phase 13."""

    __tablename__ = "job_snapshots"
    __table_args__ = (
        UniqueConstraint("posting_id", "content_hash", name="uq_job_snapshots_posting_hash"),
        Index("ix_job_snapshots_posting_scraped", "posting_id", "scraped_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_postings.id", name="fk_job_snapshots_posting"),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    location: Mapped[str | None] = mapped_column(String(200))
    employment_type: Mapped[str | None] = mapped_column(String(50))
    seniority: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    requirements: Mapped[dict | None] = mapped_column(JSONB)
    application_url: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict | None] = mapped_column(JSONB)
    provenance: Mapped[dict | None] = mapped_column(JSONB)
    source_endpoint_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), index=True
    )
    source_query_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), index=True
    )
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SearchQuery(Base):
    """Normalized search condition. Phase 13."""

    __tablename__ = "search_queries"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source", "normalized_key", name="uq_search_queries_tenant_key"
        ),
        Index("ix_search_queries_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="fresh")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_pages: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SearchResult(Base):
    """Many-to-many link between SearchQuery and JobPosting. Phase 13."""

    __tablename__ = "search_results"
    __table_args__ = (
        UniqueConstraint("query_id", "posting_id", name="uq_search_results_query_posting"),
        Index("ix_search_results_query", "query_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("search_queries.id", name="fk_search_results_query", ondelete="CASCADE"),
        nullable=False,
    )
    posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_postings.id", name="fk_search_results_posting", ondelete="CASCADE"),
        nullable=False,
    )
    rank: Mapped[int | None] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RefreshTask(Base):
    """Priority queue row for Phase 14 RefreshTask worker. Phase 13."""

    __tablename__ = "refresh_tasks"
    __table_args__ = (
        Index(
            "ix_refresh_tasks_pending",
            "tenant_id",
            "status",
            "priority",
            "scheduled_for",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="normal")
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskRecord(Base):
    """Durable audit row for a Celery-dispatched task (Phase 14.2).

    Celery's result backend is transient; this is the source of truth.
    Status transitions: ``queued → running → succeeded`` (or
    ``→ failed``); ``running → waiting_human`` parks a task at a HITL
    gate; ``→ cancelled`` is the explicit operator action.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_tasks_tenant_idempotency_key"
        ),
        Index("ix_tasks_celery_task_id", "celery_task_id"),
        Index("ix_tasks_tenant_status", "tenant_id", "status", "created_at"),
        Index("ix_tasks_kind", "tenant_id", "kind"),
        # Phase 18.3: partial DLQ index for the "Stuck / failed" tab.
        # Mirrors the migration; declared here so schema-introspection
        # tests can pin the contract.
        Index(
            "ix_tasks_dlq",
            "tenant_id",
            "dead_lettered_at",
            postgresql_where=text("status = 'dead_lettered'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    celery_task_id: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    queue: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", name="fk_tasks_parent", ondelete="SET NULL"),
        nullable=True,
    )
    trace_id: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    # Phase 18.2: structured task return value (artifact paths, ids,
    # error summaries) persisted by the postrun signal handler so
    # GET /api/tasks/{id} can hand the result back to async callers.
    result: Mapped[dict | None] = mapped_column(JSONB)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Phase 18.3: dead-letter queue plumbing. ``last_attempted_at``
    # mirrors the most-recent attempt start so the operator can tell
    # at a glance how long a row has been parked. ``dead_lettered_at``
    # + ``dlq_reason`` are populated when the task exhausts
    # ``max_retries`` instead of letting the row sit at ``failed``.
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dlq_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class GateRequest(Base):
    """HITL approval row (Phase 14.4) -- replaces the file-backed gate.

    A row is created when a task returns ``needs_human``; the worker
    is released immediately. The user's approval / rejection at
    ``/api/gate/{id}/{approve,reject}`` enqueues a follow-up task
    that resumes work under the original idempotency key.
    """

    __tablename__ = "gate_queue"
    __table_args__ = (
        Index("ix_gate_queue_tenant_status", "tenant_id", "status", "requested_at"),
        Index("ix_gate_queue_task", "task_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", name="fk_gate_queue_task", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(120))
    decision: Mapped[str | None] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text)
    ttl_seconds: Mapped[int | None] = mapped_column(Integer)


class SourceResume(Base):
    """Phase 15.1: uploaded original resume as a first-class source.

    Distinct from the user's profile YAML (evidence pool) and from
    generated outputs (``resume_versions``). The materials router
    (Phase 15.5) decides whether to ``patch_existing`` this row (DOCX
    / LaTeX) or fall back to ``generate_from_template`` when the
    source is not editable (PDF).
    """

    __tablename__ = "source_resumes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "checksum", name="uq_source_resumes_tenant_checksum"),
        Index(
            "ix_source_resumes_tenant_type",
            "tenant_id",
            "source_type",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)  # docx / latex / pdf
    editable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    original_filename: Mapped[str] = mapped_column(String(400), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(400), nullable=False)
    extracted_structure: Mapped[dict | None] = mapped_column(JSONB)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class UserDocument(Base):
    """Phase 17.8: user-curated document library.

    Distinct from :class:`SourceResume` (which is an internal Phase
    15.1 artifact used only by the materials router). ``UserDocument``
    is the first-class, user-facing library: every row was either
    (a) uploaded explicitly via the /documents API,
    (b) ingested as the side-effect of a profile-creation upload, or
    (c) promoted into the library from a generated material the user
    liked.

    The materials router can patch from any editable row here in the
    same way it patches from a SourceResume; see
    ``user_documents.to_source_resume_view`` for the adapter.
    """

    __tablename__ = "user_documents"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "document_type",
            "checksum",
            name="uq_user_documents_tenant_type_checksum",
        ),
        Index(
            "ix_user_documents_tenant_type_created",
            "tenant_id",
            "document_type",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    document_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    editable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    origin: Mapped[str] = mapped_column(String(30), nullable=False, default="uploaded")
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(400), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(400), nullable=False)
    extracted_structure: Mapped[dict | None] = mapped_column(JSONB)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    source_application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", name="fk_user_documents_application", ondelete="SET NULL"),
        nullable=True,
    )
    source_job_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ReviewQueueEntry(Base):
    """Phase 17.2: pending applications awaiting human approval.

    Created by the ``application.prepare`` task body when materials
    generation completes; transitioned through the state machine
    ``pending → approved → submitted`` (or ``pending → rejected``) by
    the operator via the Phase 17.3 ``/review`` UI. The Phase 17.5
    pre-submit hard gate re-runs ``should_refresh(..., "before_submit")``
    at the ``approved → submitted`` edge -- if the snapshot is now
    expired, the transition fails and the entry rolls back to
    ``pending`` (or to a new ``stale`` status the UI surfaces).

    Bindings
    --------
    * ``job_id`` / ``job_snapshot_id`` -- Phase 13 audit binding so the
      review-queue card always renders the JD the materials were
      generated against (even if the live JD has drifted).
    * ``materials_path`` -- where the resume+cover-letter artifacts
      were written. Phase 17.3 popover serves previews from this path.
    * ``score_breakdown`` -- snapshot of the Phase 16.1 breakdown so
      "Why was this surfaced?" works without re-scoring.

    Indexes target the kanban board's usual query: "give me all
    pending entries for tenant X ordered by created_at" / "all
    approved entries for tenant X" -- one composite index covers
    both. The unique constraint prevents the orchestrator from
    creating two pending entries for the same job snapshot in a
    single night (idempotency).
    """

    __tablename__ = "review_queue"
    __table_args__ = (
        Index(
            "ix_review_queue_tenant_status_created",
            "tenant_id",
            "status",
            "created_at",
        ),
        Index("ix_review_queue_job", "job_id"),
        Index("ix_review_queue_run_id", "run_id"),
        # Phase 17.2 codex fix: partial unique index for pending-only.
        # Prevents the orchestrator from inserting duplicate pending
        # rows for the same snapshot AND lets the same snapshot pass
        # through the lifecycle multiple times (re-run weeks later).
        Index(
            "ux_review_queue_pending_per_snapshot",
            "tenant_id",
            "job_id",
            "job_snapshot_id",
            unique=True,
            postgresql_where="status = 'pending'",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    # Nullable so the orchestrator can create an entry for a job that
    # hasn't yet been persisted (search-only mode). The Phase 17.5
    # pre-submit gate requires job_id be set before "approve and
    # submit" succeeds. Intentionally NOT a FK -- we want the entry
    # to survive retention sweeps that purge the jobs / job_snapshots
    # rows it pointed at (the kanban renders from the denormalised
    # ``company`` + ``title`` columns).
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    job_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # Free-form so legacy paths can populate strings, but the column
    # stays narrow enough for an index to be cheap.
    run_id: Mapped[str | None] = mapped_column(String(64))
    materials_path: Mapped[str | None] = mapped_column(String(400))
    score_breakdown: Mapped[dict | None] = mapped_column(JSONB)
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    portfolio_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), index=True
    )
    company: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    decision: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer: Mapped[str | None] = mapped_column(String(120))


class QABank(Base):
    __tablename__ = "qa_bank"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    question_pattern: Mapped[str | None] = mapped_column(Text)
    question_type: Mapped[str | None] = mapped_column(String(50))
    canonical_answer: Mapped[str | None] = mapped_column(Text)
    variants: Mapped[dict | None] = mapped_column(JSONB)
    confidence: Mapped[str] = mapped_column(String(20), default="high")
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)


class CleanupRun(Base):
    """Phase 18.4: per-invocation audit row for artifact cleanup.

    A row is created every time :func:`src.maintenance.artifacts.clean`
    or :func:`purge_quarantine` actually runs (manual ``autoapply
    cleanup`` invocations share this table with the
    ``maintenance.cache_eviction`` Beat task). Individual file
    decisions live in :class:`CleanupItem` keyed by ``run_id``.
    """

    __tablename__ = "cleanup_runs"
    __table_args__ = (
        Index("ix_cleanup_runs_tenant_started", "tenant_id", "started_at"),
        Index("ix_cleanup_runs_mode", "mode"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    # ``scan`` (dry-run) / ``clean`` (move to quarantine) /
    # ``purge_quarantine`` (permanent delete from quarantine) /
    # ``restore`` (move back from quarantine).
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    # Where the run was kicked off from -- ``scheduled`` (Beat),
    # ``manual`` (CLI), ``api`` (web). Recorded for forensic value
    # only; the rules are identical regardless of trigger.
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scanned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    protected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quarantined_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    purged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    restored_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_reclaimed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[dict | None] = mapped_column(JSONB)


class CleanupItem(Base):
    """Phase 18.4: per-path audit row tied to a :class:`CleanupRun`.

    One row per candidate file the run looked at. ``action`` is the
    decision (``skip_protected`` / ``skip_recent`` / ``quarantined`` /
    ``purged`` / ``restored`` / ``error``) and ``category`` is the
    classifier verdict (``protected`` / ``tmp`` / ``orphan_output`` /
    ``screenshot`` / ``version_log`` / ``unknown``). Storing both lets
    a future "show me what cleanup did last Tuesday" UI walk the rows
    without re-running the classifier.
    """

    __tablename__ = "cleanup_items"
    __table_args__ = (
        Index("ix_cleanup_items_run", "run_id"),
        Index("ix_cleanup_items_action_category", "action", "category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cleanup_runs.id", name="fk_cleanup_items_run", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    quarantine_path: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# Job Pool V2 — additive source, evaluation, portfolio, and feedback ledger.
# V1 does not read these tables; v2_shadow writes them without queue mutations.
# ---------------------------------------------------------------------------


class Employer(Base):
    __tablename__ = "employers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "normalized_name", name="uq_employers_tenant_name"),
        Index("ix_employers_tenant_name", "tenant_id", "normalized_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    normalized_name: Mapped[str] = mapped_column(String(240), nullable=False)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    aliases: Mapped[list | None] = mapped_column(JSONB)
    canonical_domain: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class EmployerAssessment(Base):
    __tablename__ = "employer_assessments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "employer_id", "classifier_version", name="uq_employer_assessment_version"
        ),
        Index("ix_employer_assessments_tenant_employer", "tenant_id", "employer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    employer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employers.id", ondelete="CASCADE"), nullable=False
    )
    classifier_version: Mapped[str] = mapped_column(String(80), nullable=False)
    employment_relationship: Mapped[str] = mapped_column(String(40), nullable=False)
    business_model: Mapped[str] = mapped_column(String(60), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(30), nullable=False)
    funding_stage: Mapped[str] = mapped_column(String(30), nullable=False)
    selectivity_tier: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[list | None] = mapped_column(JSONB)
    manual_override: Mapped[dict | None] = mapped_column(JSONB)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SourceEndpoint(Base):
    __tablename__ = "source_endpoints"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "adapter", "endpoint_key", name="uq_source_endpoints_adapter_key"
        ),
        Index("ix_source_endpoints_due", "tenant_id", "state", "next_probe_at"),
        Index("ix_source_endpoints_employer", "tenant_id", "employer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    adapter: Mapped[str] = mapped_column(String(50), nullable=False)
    endpoint_key: Mapped[str] = mapped_column(String(300), nullable=False)
    employer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employers.id", ondelete="SET NULL")
    )
    careers_url: Mapped[str | None] = mapped_column(Text)
    adapter_config: Mapped[dict | None] = mapped_column(JSONB)
    discovery_provenance: Mapped[dict | None] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="candidate")
    compliance_status: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    manual_override: Mapped[dict | None] = mapped_column(JSONB)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_empty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recovery_successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_nonempty_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"
    __table_args__ = (
        Index("ix_discovery_runs_tenant_started", "tenant_id", "started_at"),
        Index("ix_discovery_runs_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    mode: Mapped[str] = mapped_column(String(30), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(30), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_ids: Mapped[list | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running")
    counts: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceEndpointRun(Base):
    __tablename__ = "source_endpoint_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "fetch_run_id", name="uq_source_endpoint_fetch_run"),
        Index("ix_source_endpoint_runs_endpoint_started", "endpoint_id", "started_at"),
        Index("ix_source_endpoint_runs_discovery", "discovery_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_endpoints.id", ondelete="CASCADE"), nullable=False
    )
    discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discovery_runs.id", ondelete="SET NULL")
    )
    fetch_run_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    provider_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    normalized_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    malformed_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    response_signature: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceQueryArm(Base):
    __tablename__ = "source_query_arms"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "target_id",
            "adapter",
            "normalized_query",
            "geography",
            "version",
            name="uq_source_query_arm_version",
        ),
        Index("ix_source_query_arms_due", "tenant_id", "state", "next_run_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    adapter: Mapped[str] = mapped_column(String(50), nullable=False)
    query: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_query: Mapped[str] = mapped_column(String(300), nullable=False)
    geography: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    call_cost: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    useful_yield_positive: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    useful_yield_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SourceQueryRun(Base):
    __tablename__ = "source_query_runs"
    __table_args__ = (
        Index("ix_source_query_runs_arm_started", "query_arm_id", "started_at"),
        Index("ix_source_query_runs_discovery", "discovery_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    query_arm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_query_arms.id", ondelete="CASCADE"), nullable=False
    )
    discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discovery_runs.id", ondelete="SET NULL")
    )
    search_query_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_queries.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_postings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    routed_pairs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    viable_evaluations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_positives: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    applications: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobQualityAssessment(Base):
    __tablename__ = "job_quality_assessments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "snapshot_id", "classifier_version", name="uq_job_quality_snapshot_version"
        ),
        Index("ix_job_quality_snapshot", "tenant_id", "snapshot_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    classifier_version: Mapped[str] = mapped_column(String(80), nullable=False)
    assessment: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trust_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class JobTargetEvaluation(Base):
    __tablename__ = "job_target_evaluations"
    __table_args__ = (
        UniqueConstraint(
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
        Index("ix_job_target_eval_run_target", "discovery_run_id", "target_id"),
        Index("ix_job_target_eval_tier", "tenant_id", "target_id", "tier"),
        Index("ix_job_target_eval_snapshot", "tenant_id", "snapshot_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discovery_runs.id", ondelete="SET NULL")
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    candidate_version: Mapped[str] = mapped_column(String(64), nullable=False)
    target_version: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(80), nullable=False)
    role_taxonomy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_taxonomy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    scorer_version: Mapped[str] = mapped_column(String(80), nullable=False)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False, default="deterministic")
    pipeline_version: Mapped[str] = mapped_column(String(30), nullable=False)
    stage_status: Mapped[str] = mapped_column(String(30), nullable=False)
    facts: Mapped[dict] = mapped_column(JSONB, nullable=False)
    gate_results: Mapped[list] = mapped_column(JSONB, nullable=False)
    component_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)
    component_confidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    story_fit: Mapped[float] = mapped_column(Float, nullable=False)
    candidacy_index: Mapped[float] = mapped_column(Float, nullable=False)
    review_index: Mapped[float] = mapped_column(Float, nullable=False)
    adjusted_review_index: Mapped[float] = mapped_column(Float, nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    explanation: Mapped[dict] = mapped_column(JSONB, nullable=False)
    employer_assessment: Mapped[dict] = mapped_column(JSONB, nullable=False)
    posting_assessment: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DiscoveryRunEvaluation(Base):
    """Many-to-many occurrence of an immutable evaluation in discovery runs."""

    __tablename__ = "discovery_run_evaluations"
    __table_args__ = (
        UniqueConstraint("discovery_run_id", "evaluation_id", name="uq_discovery_run_evaluation"),
        Index("ix_discovery_run_evaluations_run", "discovery_run_id"),
        Index("ix_discovery_run_evaluations_evaluation", "evaluation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    discovery_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discovery_runs.id", ondelete="CASCADE"), nullable=False
    )
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_target_evaluations.id", ondelete="CASCADE"), nullable=False
    )
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class JobEvaluationReason(Base):
    __tablename__ = "job_evaluation_reasons"
    __table_args__ = (
        Index("ix_job_eval_reasons_run_target", "discovery_run_id", "target_id"),
        Index("ix_job_eval_reasons_code", "tenant_id", "stage", "reason_code"),
        Index("ix_job_eval_reasons_evaluation", "evaluation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_target_evaluations.id", ondelete="CASCADE"), nullable=False
    )
    discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discovery_runs.id", ondelete="SET NULL")
    )
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence: Mapped[list | None] = mapped_column(JSONB)
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PortfolioRun(Base):
    __tablename__ = "portfolio_runs"
    __table_args__ = (
        Index("ix_portfolio_runs_tenant_started", "tenant_id", "started_at"),
        Index("ix_portfolio_runs_discovery", "discovery_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discovery_runs.id", ondelete="SET NULL")
    )
    portfolio_id: Mapped[str] = mapped_column(String(100), nullable=False)
    portfolio_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(30), nullable=False)
    seed: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running")
    counts: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PortfolioDecision(Base):
    __tablename__ = "portfolio_decisions"
    __table_args__ = (
        UniqueConstraint("portfolio_run_id", "evaluation_id", name="uq_portfolio_decision_eval"),
        Index("ix_portfolio_decisions_run_lane", "portfolio_run_id", "lane", "selected"),
        Index("ix_portfolio_decisions_canonical", "portfolio_run_id", "canonical_group"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    portfolio_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolio_runs.id", ondelete="CASCADE"), nullable=False
    )
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_target_evaluations.id", ondelete="CASCADE"), nullable=False
    )
    canonical_group: Mapped[str] = mapped_column(String(100), nullable=False)
    owned_target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    secondary_target_ids: Mapped[list | None] = mapped_column(JSONB)
    company_key: Mapped[str] = mapped_column(String(240), nullable=False)
    lane: Mapped[str] = mapped_column(String(30), nullable=False)
    utility: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason_codes: Mapped[list | None] = mapped_column(JSONB)
    review_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ReviewFeedback(Base):
    __tablename__ = "review_feedback"
    __table_args__ = (
        Index("ix_review_feedback_target_created", "tenant_id", "target_id", "created_at"),
        Index("ix_review_feedback_evaluation", "evaluation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    review_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_target_evaluations.id", ondelete="CASCADE"), nullable=False
    )
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    judgment: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    primary_reason: Mapped[str] = mapped_column(String(100), nullable=False)
    secondary_reasons: Mapped[list | None] = mapped_column(JSONB)
    free_text: Mapped[str | None] = mapped_column(Text)
    learnable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EvaluationSet(Base):
    __tablename__ = "evaluation_sets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "version", name="uq_evaluation_set_version"),
        Index("ix_evaluation_sets_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    seed: Mapped[str] = mapped_column(String(100), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EvaluationItem(Base):
    __tablename__ = "evaluation_items"
    __table_args__ = (
        UniqueConstraint("evaluation_set_id", "snapshot_id", "target_id", name="uq_evaluation_item_pair"),
        Index("ix_evaluation_items_order", "evaluation_set_id", "presentation_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default=TENANT_DEFAULT)
    evaluation_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evaluation_sets.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_target_evaluations.id", ondelete="SET NULL")
    )
    hidden_arm: Mapped[str] = mapped_column(String(30), nullable=False)
    presentation_order: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_group: Mapped[str | None] = mapped_column(String(100))
    judgment: Mapped[str | None] = mapped_column(String(40))
    primary_reason: Mapped[str | None] = mapped_column(String(100))
    secondary_reasons: Mapped[list | None] = mapped_column(JSONB)
    judged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
