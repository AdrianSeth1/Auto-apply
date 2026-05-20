"""Phase 18.1: tests for the worker stub closeout.

The unit tests here drive each task body in eager mode with the
collaborators monkey-patched so we exercise the integration glue
without needing a live Postgres + Redis. End-to-end runs against a
real broker are tracked under Phase 18.2 / 18.3.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import pytest

from src.tasks import tasks as task_kinds
from src.tasks.app import celery_app


@pytest.fixture(autouse=True)
def _eager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", True)


def _stub_session_factory(monkeypatch: pytest.MonkeyPatch, session_obj: Any) -> None:
    @contextmanager
    def fake_factory():
        yield session_obj

    @contextmanager
    def _begin():
        yield

    if not hasattr(session_obj, "begin"):
        session_obj.begin = _begin  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "src.core.database.get_session_factory",
        lambda *args, **kwargs: fake_factory,
    )


# ---- jobs.enrich -------------------------------------------------------


def test_jobs_enrich_returns_not_found_for_unknown_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Session:
        def get(self, model, key):  # noqa: D401, ANN001
            return None

        @contextmanager
        def begin(self):
            yield

    _stub_session_factory(monkeypatch, _Session())
    out = task_kinds.jobs_enrich.apply(
        kwargs={"posting_id": str(uuid4())}
    ).get()
    assert out["status"] == "posting_not_found"


def test_jobs_enrich_rejects_invalid_uuid() -> None:
    out = task_kinds.jobs_enrich.apply(
        kwargs={"posting_id": "not-a-uuid"}
    ).get()
    assert out["status"] == "invalid_posting_id"


# ---- materials.generate ------------------------------------------------


def test_materials_generate_returns_invalid_for_bad_uuid() -> None:
    out = task_kinds.materials_generate.apply(
        kwargs={"job_id": "job-1"}
    ).get()
    # Phase 18.1: an invalid job_id reports it directly instead of
    # silently returning "scheduled".
    assert out["status"] == "invalid_job_id"
    assert out["document_types"] == ["resume", "cover_letter"]


def test_materials_generate_handles_missing_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Session:
        def get(self, model, key):  # noqa: D401, ANN001
            return None

        @contextmanager
        def begin(self):
            yield

    _stub_session_factory(monkeypatch, _Session())
    out = task_kinds.materials_generate.apply(
        kwargs={"job_id": str(uuid4())}
    ).get()
    assert out["status"] == "posting_not_found"


# ---- application.prepare ----------------------------------------------


def test_application_prepare_returns_invalid_for_bad_uuid() -> None:
    out = task_kinds.application_prepare.apply(
        kwargs={"application_id": "app-99"}
    ).get()
    assert out["status"] == "invalid_id"


# ---- application.fill: explicit not_implemented -----------------------


def test_application_fill_returns_not_implemented() -> None:
    out = task_kinds.application_fill.apply(
        kwargs={"application_id": str(uuid4())}
    ).get()
    assert out["status"] == "not_implemented"
    assert "Playwright" in out["detail"]


# ---- application.submit: pre-submit gate wiring -----------------------


def test_application_submit_returns_invalid_for_bad_uuid() -> None:
    out = task_kinds.application_submit.apply(
        kwargs={"application_id": "app-99"}
    ).get()
    assert out["status"] == "invalid_id"
    assert out["application_id"] == "app-99"


def test_application_submit_reports_missing_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Session:
        def get(self, model, key):  # noqa: D401, ANN001
            return None

        @contextmanager
        def begin(self):
            yield

    _stub_session_factory(monkeypatch, _Session())
    out = task_kinds.application_submit.apply(
        kwargs={"application_id": str(uuid4())}
    ).get()
    assert out["status"] == "application_not_found"


# ---- status_sync: explicit not_implemented ----------------------------


def test_status_sync_returns_not_implemented() -> None:
    out_a = task_kinds.status_sync.apply(kwargs={}).get()
    out_b = task_kinds.status_sync.apply(
        kwargs={"application_id": "x"}
    ).get()
    assert out_a["status"] == "not_implemented"
    assert out_b["status"] == "not_implemented"


# ---- maintenance.gate_expire_sweep ------------------------------------


def test_gate_expire_sweep_with_no_pending_rows_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Result:
        def scalars(self):
            return iter(())

    class _Session:
        def execute(self, _stmt):
            return _Result()

        @contextmanager
        def begin(self):
            yield

    _stub_session_factory(monkeypatch, _Session())
    out = task_kinds.gate_expire_sweep.apply().get()
    assert out["status"] == "ok"
    assert out["expired"] == 0


# ---- maintenance.jd_health_check --------------------------------------


def test_jd_health_check_with_no_postings_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Result:
        def scalars(self):
            return iter(())

    class _Session:
        def execute(self, _stmt):
            return _Result()

        @contextmanager
        def begin(self):
            yield

    _stub_session_factory(monkeypatch, _Session())
    out = task_kinds.jd_health_check.apply().get()
    assert out["status"] == "ok"
    assert out["examined"] == 0
    assert out["transitions"] == {}


# ---- maintenance.linkedin_cookie_refresh ------------------------------


def test_linkedin_cookie_refresh_handles_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(**_kwargs):
        raise RuntimeError("simulated probe failure")

    monkeypatch.setattr(
        "src.intake.linkedin.get_linkedin_session_status", _boom
    )
    out = task_kinds.linkedin_cookie_refresh.apply().get()
    assert out["status"] == "error"
    assert "simulated probe failure" in out["error"]


def test_linkedin_cookie_refresh_returns_probe_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _ok(**_kwargs):
        return {
            "ok": True,
            "authenticated": True,
            "has_session_data": True,
            "message": "all good",
            "checked_at": "2026-05-20T00:00:00+00:00",
        }

    monkeypatch.setattr(
        "src.intake.linkedin.get_linkedin_session_status", _ok
    )
    out = task_kinds.linkedin_cookie_refresh.apply().get()
    assert out["status"] == "ok"
    assert out["authenticated"] is True
