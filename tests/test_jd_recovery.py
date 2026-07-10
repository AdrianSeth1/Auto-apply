"""Tests for JD recovery (src/intake/jd_recovery.py) and the thin-JD
gate wired into materials.generate (src/tasks/tasks.py).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import pytest

from src.intake import jd_recovery
from src.tasks import tasks as task_kinds
from src.tasks.app import celery_app

LONG_PARAGRAPH = (
    "We are looking for a Senior Software Engineer to join our platform "
    "team. You will design and build distributed systems that power our "
    "core product, working closely with product managers and designers "
    "to ship features used by millions of people every day. Requirements: "
    "5+ years of experience with Python or Go, strong understanding of "
    "distributed systems, database design, and API development. You "
    "should be comfortable owning projects end to end, from design "
    "through deployment and on-call support. We offer competitive pay, "
    "equity, health insurance, and a fully remote-friendly culture. "
    "Nice to have: experience with Kubernetes, Terraform, or large-scale "
    "data pipelines. "
) * 2

FIXTURE_HTML = f"""
<html>
<head><script>var x = 1;</script><style>.a {{ color: red; }}</style></head>
<body>
<nav><a href="/">Home</a><a href="/about">About</a><a href="/jobs">Jobs</a></nav>
<header><h1>Acme Corp Careers</h1></header>
<main>
<h2>Senior Software Engineer</h2>
<p>{LONG_PARAGRAPH}</p>
</main>
<footer>Copyright 2026 Acme Corp. All rights reserved. Privacy | Terms</footer>
</body>
</html>
"""


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": content_type}


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[str] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def get(self, url: str, **_kwargs: Any) -> _FakeResponse:
        self.calls.append(url)
        return self._response


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> None:
    monkeypatch.setattr(
        jd_recovery.httpx, "Client", lambda **_kwargs: _FakeClient(response)
    )


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    from src.cache import reset_cache

    reset_cache()
    yield
    reset_cache()


# ---- recover_job_description: extraction heuristic --------------------


def test_recover_job_description_extracts_main_content_not_nav(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_httpx(monkeypatch, _FakeResponse(text=FIXTURE_HTML))

    result = jd_recovery.recover_job_description("https://acme.example/careers/123")

    assert result is not None
    assert "Senior Software Engineer" in result or "distributed systems" in result
    assert "Home" not in result
    assert "Copyright 2026 Acme Corp" not in result


# ---- <300-char rejection ------------------------------------------------


def test_recover_job_description_rejects_short_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = "<html><body><main><p>Too short.</p></main></body></html>"
    _patch_httpx(monkeypatch, _FakeResponse(text=html))

    result = jd_recovery.recover_job_description("https://acme.example/careers/456")

    assert result is None


# ---- non-HTML rejection --------------------------------------------------


def test_recover_job_description_rejects_non_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_httpx(
        monkeypatch,
        _FakeResponse(text='{"ok": true}', content_type="application/json"),
    )

    result = jd_recovery.recover_job_description("https://acme.example/api/job/789")

    assert result is None


def test_recover_job_description_rejects_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_httpx(monkeypatch, _FakeResponse(status_code=404, text=FIXTURE_HTML))

    result = jd_recovery.recover_job_description("https://acme.example/careers/gone")

    assert result is None


def test_recover_job_description_returns_none_on_empty_url() -> None:
    assert jd_recovery.recover_job_description("") is None


# ---- recovery-then-parse happy path --------------------------------------


def test_recovery_then_parse_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.intake.jd_parser import parse_requirements

    _patch_httpx(monkeypatch, _FakeResponse(text=FIXTURE_HTML))

    recovered = jd_recovery.recover_job_description(
        "https://acme.example/careers/123"
    )
    assert recovered is not None
    assert len(recovered) >= 300

    requirements = parse_requirements(recovered, use_llm=False)
    # Regex fallback should pick up the explicit years-of-experience
    # requirement out of the recovered text.
    assert requirements.experience_years_min == 5


# ---- thin-JD gate wired into materials.generate --------------------------


def _stub_session_factory(monkeypatch: pytest.MonkeyPatch, session_obj: Any) -> None:
    @contextmanager
    def fake_factory():
        yield session_obj

    monkeypatch.setattr(
        "src.core.database.get_session_factory",
        lambda *args, **kwargs: fake_factory,
    )


@pytest.fixture(autouse=True)
def _eager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", True)


def test_materials_generate_thin_jd_gate_sets_review_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core.models import JobPosting, JobSnapshot

    posting_id = uuid4()
    snapshot_id = uuid4()

    class _FakePosting:
        id = posting_id
        source = "greenhouse"
        source_id = "123"
        company = "Acme"
        latest_snapshot_id = snapshot_id

    class _FakeSnapshot:
        title = "Software Engineer"
        location = "Remote"
        employment_type = "full_time"
        seniority = "mid"
        description = "short stub"
        requirements = None
        application_url = None  # no URL -> recovery is skipped entirely
        raw_data = None

    class _FakeEntry:
        def __init__(self) -> None:
            self.reason = None

    entry = _FakeEntry()

    class _Result:
        def __init__(self, row: Any) -> None:
            self._row = row

        def scalar_one_or_none(self) -> Any:
            return self._row

    class _Session:
        def get(self, model: Any, key: Any) -> Any:
            if model is JobPosting:
                return _FakePosting()
            if model is JobSnapshot:
                return _FakeSnapshot()
            return None

        def execute(self, _stmt: Any) -> _Result:
            return _Result(entry)

        @contextmanager
        def begin(self):
            yield

    _stub_session_factory(monkeypatch, _Session())

    out = task_kinds.materials_generate.apply(
        kwargs={"job_id": str(posting_id)}
    ).get()

    assert out["status"] == "thin_jd"
    assert out["description_chars"] == len("short stub")
    assert entry.reason is not None
    assert "too thin" in entry.reason
    assert "10ch" in entry.reason


def test_materials_generate_attempts_recovery_before_gating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core.models import JobPosting, JobSnapshot

    posting_id = uuid4()
    snapshot_id = uuid4()

    class _FakePosting:
        id = posting_id
        source = "greenhouse"
        source_id = "123"
        company = "Acme"
        latest_snapshot_id = snapshot_id

    class _FakeSnapshot:
        title = "Software Engineer"
        location = "Remote"
        employment_type = "full_time"
        seniority = "mid"
        description = "short stub"
        requirements = None
        application_url = "https://acme.example/careers/123"
        raw_data = None

    class _Session:
        def get(self, model: Any, key: Any) -> Any:
            if model is JobPosting:
                return _FakePosting()
            if model is JobSnapshot:
                return _FakeSnapshot()
            return None

        def execute(self, _stmt: Any) -> Any:
            class _Result:
                def scalar_one_or_none(self) -> None:
                    return None

            return _Result()

        @contextmanager
        def begin(self):
            yield

    _stub_session_factory(monkeypatch, _Session())
    monkeypatch.setattr(
        "src.intake.jd_recovery.recover_job_description",
        lambda url, **_kwargs: None,
    )

    out = task_kinds.materials_generate.apply(
        kwargs={"job_id": str(posting_id)}
    ).get()

    # Recovery was attempted (application_url present) but failed, so
    # the gate still fires with the original short description length.
    assert out["status"] == "thin_jd"
    assert out["description_chars"] == len("short stub")
