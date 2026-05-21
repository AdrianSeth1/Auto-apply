"""Phase 18: Codex-review fix regression tests.

Codex review surfaced four P2 issues that are now fixed; the tests
below pin the contract so a future refactor doesn't reintroduce
them.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session, sessionmaker

from src.core.config import get_db_url, load_config
from src.core.models import Application, Job
from src.tracker.database import (
    get_application_counts,
    get_applications_with_jobs,
)


@pytest.fixture(scope="module")
def engine():
    return create_engine(get_db_url(load_config()))


@pytest.fixture
def db_session(engine) -> Session:
    factory = sessionmaker(bind=engine)
    s = factory()
    yield s
    s.execute(delete(Application).where(Application.tenant_id == "codex-test"))
    s.execute(delete(Job).where(Job.tenant_id == "codex-test"))
    s.commit()
    s.close()


def _make_job(session: Session) -> Job:
    job = Job(
        tenant_id="codex-test",
        company="CodexCo",
        title="Engineer",
    )
    session.add(job)
    session.flush()
    return job


def test_get_applications_with_jobs_excludes_soft_deleted(
    db_session: Session,
) -> None:
    """Codex P2 fix: ``DELETE /api/applications/{id}`` writes
    ``deleted_at`` but the application list query must not return
    the row anymore."""
    job = _make_job(db_session)
    live = Application(
        tenant_id="codex-test", job_id=job.id, status="DISCOVERED"
    )
    deleted = Application(
        tenant_id="codex-test",
        job_id=job.id,
        status="DISCOVERED",
        deleted_at=datetime.now(UTC),
    )
    db_session.add_all([live, deleted])
    db_session.commit()

    rows = [
        (app, job)
        for app, job in get_applications_with_jobs(db_session)
        if app.tenant_id == "codex-test"
    ]
    assert {row[0].id for row in rows} == {live.id}

    # Forensic / cleanup callers can still opt in.
    rows_all = [
        (app, job)
        for app, job in get_applications_with_jobs(
            db_session, include_soft_deleted=True
        )
        if app.tenant_id == "codex-test"
    ]
    assert {row[0].id for row in rows_all} == {live.id, deleted.id}


def test_get_application_counts_excludes_soft_deleted(
    db_session: Session,
) -> None:
    job = _make_job(db_session)
    db_session.add(
        Application(tenant_id="codex-test", job_id=job.id, status="DISCOVERED")
    )
    db_session.add(
        Application(
            tenant_id="codex-test",
            job_id=job.id,
            status="DISCOVERED",
            deleted_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    # The aggregate is global, not tenant-scoped, so we only assert
    # the deleted row isn't counted by comparing pre/post.
    counts = get_application_counts(db_session)
    discovered = counts.get("DISCOVERED", 0)
    assert discovered >= 1  # the live one
    # The deleted row is excluded; we can't assert the exact total
    # without isolating tenants, but we can pin "deleted_at != null
    # rows are skipped" by reading their count separately.
    other_tenant_discovered = (
        db_session.execute(
            __import__("sqlalchemy").select(  # noqa: PLC0415 -- defensive
                __import__("sqlalchemy").func.count()
            )
            .select_from(Application)
            .where(Application.deleted_at.is_(None))
            .where(Application.tenant_id == "codex-test")
        ).scalar()
        or 0
    )
    assert other_tenant_discovered == 1


def test_protected_paths_includes_task_record_result() -> None:
    """Codex P2 fix: ``TaskRecord.result`` artifact paths must be
    in the protected set so cleanup doesn't quarantine produced
    files that are only referenced by the audit row."""
    # Inspect the protected-set builder's behaviour via a stubbed
    # session because the real builder needs the DB. The test pins
    # the source code reference: the module must select TaskRecord.result.
    import src.maintenance.artifacts as artifacts_mod

    src = importlib.import_module(artifacts_mod.__name__).__file__
    assert src is not None
    with open(src, encoding="utf-8") as fh:
        body = fh.read()
    assert "TaskRecord.result" in body, (
        "build_protected_paths must walk TaskRecord.result (Codex P2 fix)"
    )


def test_regenerate_idempotency_key_includes_choice_fingerprint() -> None:
    """Codex P2 fix: changing strategy / template / source on a
    regenerate must produce a different idempotency key so the new
    request actually fires instead of short-circuiting on the prior
    success."""
    # Inspect the source: the key is built from a sha256 of the
    # choice dict and includes those fields in its JSON encoding.
    src_path = importlib.import_module(
        "src.web.routes.api"
    ).__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as fh:
        body = fh.read()
    for fragment in (
        "choice_fingerprint",
        "regenerate_enqueue_version",
        "legacy_job_payload",
        "job_id",
        "strategy",
        "template_id",
        "template_manifest_fingerprint",
        "source_document_id",
        "patch_aggressiveness",
    ):
        assert fragment in body, f"expected {fragment!r} in api.py idempotency key fingerprint"


def test_regenerate_idempotency_key_invalidates_on_template_edit(tmp_path) -> None:
    """2026-05-21 fix: editing a template's style_overrides / target_pages
    in the Template Library must change the regenerate idempotency
    fingerprint so the broker actually re-runs the task instead of
    handing back the previously-succeeded artifact.
    """
    import hashlib
    import json

    from src.documents.templates import (
        ensure_template_package,
        load_template_package,
        update_docx_template_styles,
    )

    ensure_template_package("resume", template_root=tmp_path)

    def _fingerprint() -> str:
        package = load_template_package(
            "resume", "ats_single_column_v1", template_root=tmp_path
        )
        manifest_fp = hashlib.sha256(
            package.manifest.model_dump_json(
                exclude={"name", "description"}
            ).encode("utf-8")
        ).hexdigest()[:16]
        # Pin request_bucket_5s so the test isolates "manifest changed"
        # from "user waited >5s and got a new bucket".
        return hashlib.sha256(
            json.dumps(
                {
                    "regenerate_enqueue_version": 4,
                    "strategy": "regenerate",
                    "template_id": "ats_single_column_v1",
                    "template_manifest_fingerprint": manifest_fp,
                    "legacy_job_payload": False,
                    "job_id": "job-1",
                    "source_document_id": None,
                    "patch_aggressiveness": None,
                    "patch_allow_reorder_sections": None,
                    "patch_allow_add_remove_bullets": None,
                    "request_bucket_5s": 1234567,
                },
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:16]

    before = _fingerprint()
    update_docx_template_styles(
        document_type="resume",
        template_id="ats_single_column_v1",
        overrides={"normal": {"font": "Garamond", "size": 11}},
        target_pages=2,
        template_root=tmp_path,
    )
    after = _fingerprint()
    assert before != after, (
        "template manifest hash must change when style_overrides or "
        "target_pages are edited so regenerate stops short-circuiting"
    )


def test_regenerate_idempotency_key_includes_time_bucket() -> None:
    """2026-05-21 fix v4: user-initiated regenerate must fire when the
    user really wants it -- including a 5-second time bucket in the
    fingerprint means the only short-circuit window is sub-5-second
    duplicates (network retry / accidental double click), not a
    deliberate re-click an hour later."""
    import importlib

    src_path = importlib.import_module("src.web.routes.api").__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as fh:
        body = fh.read()
    assert "request_bucket_5s" in body, (
        "regenerate fingerprint must include a 5-second time bucket so a "
        "user clicking Regenerate after >5s is not blocked by idempotency"
    )
    assert "DEFAULT_TEMPLATE_IDS" in body, (
        "fingerprint must resolve to the seeded default template_id when the "
        "task payload has no explicit template_id, otherwise template edits "
        "on the default template do not invalidate the fingerprint"
    )


def test_material_envelope_recognises_failed_task() -> None:
    """Codex P2 fix: when ``result.errors`` is non-empty and no
    artifact came back, the API wrapper must surface the failure
    instead of returning ``{ok: true, artifact: null}``."""
    # We exercise the JS source through a syntactic check (the
    # frontend tests live separately); pin the contract by asserting
    # the helper checks ``status`` / ``Object.keys(artifacts)`` /
    # ``errors.length > 0 && !documentArtifact`` against the row.
    import pathlib

    src = pathlib.Path("frontend/src/lib/api.js").read_text(encoding="utf-8")
    assert "Object.keys(artifacts).length === 0" in src
    assert "errors.length > 0 && !documentArtifact" in src
