"""SUP-02: scripts/probe_employer_cohort.py.

Pure Python -- no network, no database. ``scripts/`` has no ``__init__.py``
and pyproject.toml's ``[tool.setuptools] packages = ["src"]`` doesn't
install it as an importable package, so the module under test is loaded
directly from its file path instead of via a dotted import. All HTTP is
faked at the ``httpx.Client`` / adapter-scraper-class boundary, matching the
pattern already proven in ``tests/test_intake_endpoint_metrics.py``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import yaml

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "probe_employer_cohort.py"
_SPEC = importlib.util.spec_from_file_location("probe_employer_cohort", _MODULE_PATH)
pec = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = pec
_SPEC.loader.exec_module(pec)

from src.intake.base import ScraperError  # noqa: E402 -- after dynamic module load, matches its own import order
from src.intake.schema import RawJob  # noqa: E402


def _job(
    *,
    source="greenhouse",
    source_id="1",
    company="Acme",
    location="Remote",
    description=None,
    application_url="https://boards.greenhouse.io/acme/jobs/1",
):
    return RawJob(
        source=source,
        source_id=source_id,
        company=company,
        title="Engineer",
        location=location,
        description=description,
        application_url=application_url,
    )


class _FakeResponse:
    def __init__(self, *, url, status_code=200, text=""):
        self.url = url
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """Duck-typed stand-in for httpx.Client -- probe_employer only calls .get()."""

    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls: list[str] = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        if self._exc is not None:
            raise self._exc
        return self._response


class _FakeRenderer:
    """Duck-typed stand-in for BrowserRenderer -- probe_employer only calls
    .render(url) and expects something with .html / .final_url / .error."""

    def __init__(self, result):
        self._result = result
        self.calls: list[str] = []

    def render(self, url):
        self.calls.append(url)
        return self._result


def _fake_scraper_class(*, jobs=None, error=None, call_log=None):
    class _Fake:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            if call_log is not None:
                call_log.append(1)
            return self

        def __exit__(self, *exc_info):
            return False

        def fetch_jobs(self, slug):
            if error is not None:
                raise error
            return list(jobs or [])

    return _Fake


# ---------------------------------------------------------------------------
# detect_adapter_candidates
# ---------------------------------------------------------------------------


def test_detects_greenhouse_link_in_html():
    html = '<a href="https://boards.greenhouse.io/acme/jobs/123">Careers</a>'
    candidates = pec.detect_adapter_candidates("https://acme.com/careers", html)
    assert any(c.adapter == "greenhouse" and c.endpoint_config == "acme" for c in candidates)


def test_lever_slug_dedup_key_is_lowercased_but_stored_value_keeps_original_case():
    html = '<a href="https://jobs.lever.co/AcmeCo/abc123">Apply</a>'
    candidates = pec.detect_adapter_candidates("https://acme.com/careers", html)
    match = next(c for c in candidates if c.adapter == "lever")
    assert match.endpoint_config == "AcmeCo"


def test_workday_tenant_with_site_is_a_usable_candidate():
    html = '<a href="https://acme.wd5.myworkdayjobs.com/en-US/External">Careers</a>'
    candidates = pec.detect_adapter_candidates("https://acme.com/careers", html)
    match = next(c for c in candidates if c.adapter == "workday")
    assert match.endpoint_config == {"tenant": "acme", "host": "wd5", "site": "External"}


def test_workday_tenant_without_site_is_recorded_but_unusable():
    """SUP-02: 'the site segment is fragile/best-effort' -- detected-but-
    incomplete stays visible for review instead of being silently dropped
    or guessed."""
    html = '<a href="https://acme.wd5.myworkdayjobs.com/">Careers</a>'
    candidates = pec.detect_adapter_candidates("https://acme.com/careers", html)
    match = next(c for c in candidates if c.adapter == "workday")
    assert match.endpoint_config is None


def test_static_public_link_detection_is_not_shadowed_by_api_patterns():
    """Regression guard: _ADAPTER_PATTERNS and _API_PATTERNS share adapter
    keys (both have 'greenhouse' etc). A dict-merge scan (e.g. {**a, **b})
    would silently drop one pattern per adapter on the key collision --
    this must scan both without either shadowing the other."""
    html = '<a href="https://boards.greenhouse.io/acme/jobs/1">Careers</a>'
    candidates = pec.detect_adapter_candidates("https://acme.com/careers", html)
    assert any(c.adapter == "greenhouse" and c.endpoint_config == "acme" for c in candidates)


def test_greenhouse_api_network_url_is_detected():
    """React/Next careers pages often fetch the ATS API directly via
    client-side JS and never render a plain <a href> link -- the slug only
    ever shows up in a network request URL."""
    network_text = "https://boards-api.greenhouse.io/v1/boards/acmeworks/jobs"
    candidates = pec.detect_adapter_candidates("https://acme.com/careers", network_text)
    assert any(c.adapter == "greenhouse" and c.endpoint_config == "acmeworks" for c in candidates)


def test_lever_api_network_url_is_detected():
    network_text = "https://api.lever.co/v0/postings/acme-inc?mode=json"
    candidates = pec.detect_adapter_candidates("https://acme.com/careers", network_text)
    assert any(c.adapter == "lever" and c.endpoint_config == "acme-inc" for c in candidates)


def test_ashby_api_network_url_is_detected():
    network_text = "https://api.ashbyhq.com/posting-api/job-board/acme"
    candidates = pec.detect_adapter_candidates("https://acme.com/careers", network_text)
    assert any(c.adapter == "ashby" and c.endpoint_config == "acme" for c in candidates)


def test_smartrecruiters_api_network_url_is_detected():
    network_text = "https://api.smartrecruiters.com/v1/companies/AcmeCorp/postings"
    candidates = pec.detect_adapter_candidates("https://acme.com/careers", network_text)
    assert any(c.adapter == "smartrecruiters" and c.endpoint_config == "AcmeCorp" for c in candidates)


def test_workable_api_network_url_extracts_real_slug_not_the_word_api():
    """Workable's API and public link share a host (apply.workable.com);
    the API path shape (/api/v3/accounts/{slug}/jobs) must not let the
    public-link pattern misread the literal 'api' path segment as the slug."""
    network_text = "https://apply.workable.com/api/v3/accounts/acme-corp/jobs"
    candidates = pec.detect_adapter_candidates("https://acme.com/careers", network_text)
    assert any(c.adapter == "workable" and c.endpoint_config == "acme-corp" for c in candidates)
    assert not any(c.adapter == "workable" and c.endpoint_config == "api" for c in candidates)


def test_workday_cxs_api_url_resolves_real_site_not_wday():
    """The real Workday CXS API call shape (.../wday/cxs/{tenant}/{site}/jobs)
    must resolve the true site -- and the generic page-link pattern, which
    also matches this same URL, must not misread the literal 'wday' path
    segment as the site."""
    network_text = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/jobs"
    candidates = pec.detect_adapter_candidates("https://acme.com/careers", network_text)
    usable = [c for c in candidates if c.adapter == "workday" and c.endpoint_config is not None]
    assert len(usable) == 1
    assert usable[0].endpoint_config == {"tenant": "acme", "host": "wd5", "site": "External"}


def test_most_referenced_candidate_sorts_first():
    html = (
        '<a href="https://boards.greenhouse.io/acme/jobs/1">A</a>'
        '<a href="https://boards.greenhouse.io/acme/jobs/2">B</a>'
        '<a href="https://jobs.lever.co/other/xyz">C</a>'
    )
    candidates = pec.detect_adapter_candidates("https://acme.com/careers", html)
    assert candidates[0].adapter == "greenhouse"
    assert candidates[0].occurrences == 2


def test_no_recognized_pattern_returns_no_candidates():
    html = "<p>We use a custom in-house applicant tracker.</p>"
    candidates = pec.detect_adapter_candidates("https://acme.com/careers", html)
    assert candidates == []


# ---------------------------------------------------------------------------
# verify_sample / _identity_match
# ---------------------------------------------------------------------------


def test_verify_sample_passes_clean_full_sample():
    jobs = [_job(description="x" * 400)]
    checks = pec.verify_sample(employer_name="Acme", adapter="greenhouse", jobs=jobs)
    assert checks["passed"] is True


def test_verify_sample_fails_closed_on_empty_sample():
    """An empty sample must fail every check, never vacuously pass."""
    checks = pec.verify_sample(employer_name="Acme", adapter="greenhouse", jobs=[])
    assert all(value is False for value in checks.values())


def test_verify_sample_fails_when_application_url_is_third_party_redirect():
    jobs = [
        _job(
            description="x" * 400,
            application_url="https://some-aggregator.example.com/redirect?to=acme",
        )
    ]
    checks = pec.verify_sample(employer_name="Acme", adapter="greenhouse", jobs=jobs)
    assert checks["direct_application_urls"] is False
    assert checks["passed"] is False


def test_verify_sample_fails_on_duplicate_ids():
    jobs = [
        _job(source_id="1", description="x" * 400),
        _job(source_id="1", description="x" * 400),
    ]
    checks = pec.verify_sample(employer_name="Acme", adapter="greenhouse", jobs=jobs)
    assert checks["unique_nonempty_ids"] is False


def test_verify_sample_fails_when_identity_does_not_match():
    jobs = [_job(company="Totally Unrelated Corp", description="x" * 400)]
    checks = pec.verify_sample(employer_name="Acme", adapter="greenhouse", jobs=jobs)
    assert checks["employer_identity_match"] is False


def test_verify_sample_fails_when_description_is_snippet_length():
    jobs = [_job(description="too short")]
    checks = pec.verify_sample(employer_name="Acme", adapter="greenhouse", jobs=jobs)
    assert checks["has_full_description"] is False


def test_identity_match_handles_slug_derived_company_field_without_spaces():
    assert pec._identity_match("Modern Health", "Modernhealth") is True


def test_identity_match_rejects_unrelated_names():
    assert pec._identity_match("Modern Health", "Totally Unrelated Corp") is False


# ---------------------------------------------------------------------------
# fetch_sample
# ---------------------------------------------------------------------------


def test_fetch_sample_uses_registered_scraper_class_and_returns_no_error(monkeypatch):
    job = _job()
    call_log: list[int] = []
    fake = _fake_scraper_class(jobs=[job], call_log=call_log)
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "greenhouse", fake)

    jobs, error = pec.fetch_sample("greenhouse", "acme")

    assert jobs == [job]
    assert error is None
    assert call_log == [1]


def test_fetch_sample_reports_scraper_error_without_raising(monkeypatch):
    fake = _fake_scraper_class(error=ScraperError("HTTP 500 from acme"))
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "greenhouse", fake)

    jobs, error = pec.fetch_sample("greenhouse", "acme")

    assert jobs == []
    assert "HTTP 500" in error


def test_fetch_sample_survives_unexpected_exception_instead_of_crashing(monkeypatch):
    fake = _fake_scraper_class(error=ValueError("boom"))
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "greenhouse", fake)

    jobs, error = pec.fetch_sample("greenhouse", "acme")

    assert jobs == []
    assert "boom" in error


def test_fetch_sample_unknown_adapter_returns_error_not_crash():
    jobs, error = pec.fetch_sample("carbon-dated-paper-forms", "acme")
    assert jobs == []
    assert "no scraper registered" in error


def test_fetch_sample_slices_before_returning(monkeypatch):
    jobs = [_job(source_id=str(i)) for i in range(10)]
    fake = _fake_scraper_class(jobs=jobs)
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "greenhouse", fake)

    sample, error = pec.fetch_sample("greenhouse", "acme", sample_size=3)

    assert len(sample) == 3
    assert error is None


def test_fetch_sample_enriches_workday_sample_with_full_description(monkeypatch):
    """SUP-02 follow-up: Workday's list endpoint carries no description at
    all (src/intake/workday.py), so a fair verification requires the same
    fetch_job_detail() step the real pipeline uses -- otherwise every
    Workday candidate fails has_full_description regardless of endpoint
    quality."""
    stub_job = _job(source="workday", description=None, application_url="https://acme.wd1.myworkdayjobs.com/job/1")
    detail_log: list[object] = []

    class _FakeWorkday:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def fetch_jobs(self, tenant_config):
            return [stub_job]

        def fetch_job_detail(self, job):
            detail_log.append(job)
            return _job(
                source="workday",
                source_id=job.source_id,
                description="x" * 400,
                application_url=job.application_url,
            )

    monkeypatch.setitem(pec._SCRAPER_CLASSES, "workday", _FakeWorkday)

    sample, error = pec.fetch_sample(
        "workday", {"tenant": "acme", "host": "wd1", "site": "External"}, sample_size=8
    )

    assert error is None
    assert len(detail_log) == 1  # detail-fetch was actually invoked
    assert sample[0].description == "x" * 400


def test_fetch_sample_does_not_detail_fetch_non_workday_adapters(monkeypatch):
    """A non-Workday fake scraper with no fetch_job_detail method at all
    must not be touched -- only Workday needs the extra step."""
    fake = _fake_scraper_class(jobs=[_job()])
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "greenhouse", fake)

    sample, error = pec.fetch_sample("greenhouse", "acme", sample_size=8)

    assert error is None
    assert len(sample) == 1


# ---------------------------------------------------------------------------
# BrowserRenderer
# ---------------------------------------------------------------------------


def test_browser_renderer_does_not_launch_until_render_is_called():
    """Lazy by design -- an employer whose static HTML already has a usable
    link must never pay for a Chromium launch."""
    renderer = pec.BrowserRenderer()
    assert renderer._browser is None
    assert renderer._playwright is None
    renderer.close()  # must be a safe no-op when nothing was ever started
    assert renderer._browser is None


def test_browser_renderer_degrades_gracefully_when_unavailable(monkeypatch):
    """A missing/broken Playwright install must produce a RenderResult
    error, never raise -- one bad browser must not crash the probe run."""
    renderer = pec.BrowserRenderer()
    monkeypatch.setattr(renderer, "_ensure_started", lambda: "playwright not installed: simulated")

    result = renderer.render("https://acme.com/careers")

    assert result.html == ""
    assert result.error == "playwright not installed: simulated"


# ---------------------------------------------------------------------------
# probe_employer
# ---------------------------------------------------------------------------


def test_probe_employer_end_to_end_verified(monkeypatch):
    html = '<a href="https://boards.greenhouse.io/acme/jobs/1">Careers</a>'
    client = _FakeClient(_FakeResponse(url="https://acme.com/careers", text=html))
    job = _job(company="Acme", description="y" * 400)
    fake = _fake_scraper_class(jobs=[job])
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "greenhouse", fake)

    employer = {"name": "Acme", "careers_url": "https://acme.com/careers", "targets": ["ai-implementation"]}
    result = pec.probe_employer(client, employer, sample_size=8, fetch_timeout=10)

    assert result.status == "probed"
    assert result.adapter == "greenhouse"
    assert result.endpoint_config == "acme"
    assert result.verified is True
    assert result.checks["passed"] is True
    assert result.error is None


def test_probe_employer_marks_adapter_not_detected_without_touching_any_scraper(monkeypatch):
    client = _FakeClient(_FakeResponse(url="https://acme.com/careers", text="<p>Custom ATS</p>"))
    call_log: list[int] = []
    fake = _fake_scraper_class(jobs=[_job()], call_log=call_log)
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "greenhouse", fake)

    employer = {"name": "Acme", "careers_url": "https://acme.com/careers", "targets": []}
    result = pec.probe_employer(client, employer, sample_size=8, fetch_timeout=10)

    assert result.status == "adapter_not_detected"
    assert result.verified is False
    assert call_log == []  # never fetched a sample for an undetected adapter


def test_probe_employer_marks_workday_site_unresolved(monkeypatch):
    html = '<a href="https://acme.wd5.myworkdayjobs.com/">Careers</a>'
    client = _FakeClient(_FakeResponse(url="https://acme.com/careers", text=html))
    call_log: list[int] = []
    fake = _fake_scraper_class(jobs=[_job()], call_log=call_log)
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "workday", fake)

    employer = {"name": "Acme", "careers_url": "https://acme.com/careers", "targets": []}
    result = pec.probe_employer(client, employer, sample_size=8, fetch_timeout=10)

    assert result.status == "workday_site_unresolved"
    assert result.verified is False
    assert call_log == []


def test_probe_employer_falls_back_to_js_render_when_static_finds_nothing(monkeypatch):
    """A JS-rendered careers page: static HTML has nothing, but the
    renderer sees the real widget. Recovered candidate is tagged
    detection_method='js_render' for audit transparency."""
    client = _FakeClient(_FakeResponse(url="https://acme.com/careers", text="<p>loading...</p>"))
    renderer = _FakeRenderer(
        pec.RenderResult(
            html='<a href="https://boards.greenhouse.io/acme/jobs/1">Careers</a>',
            final_url="https://acme.com/careers",
            error=None,
        )
    )
    job = _job(company="Acme", description="y" * 400)
    fake = _fake_scraper_class(jobs=[job])
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "greenhouse", fake)

    employer = {"name": "Acme", "careers_url": "https://acme.com/careers", "targets": []}
    result = pec.probe_employer(client, employer, sample_size=8, fetch_timeout=10, renderer=renderer)

    assert len(renderer.calls) == 1
    assert result.status == "probed"
    assert result.adapter == "greenhouse"
    assert result.detection_method == "js_render"
    assert result.render_used is True
    assert result.verified is True


def test_probe_employer_detects_endpoint_from_network_request_when_dom_html_has_nothing(monkeypatch):
    """A React careers page that fetches its ATS API via client-side JS and
    never renders a plain <a href> link at all -- page.content() comes back
    with nothing useful, but the API call shows up in captured network
    requests. This is the exact gap that left 12/35 employers at
    adapter_not_detected with render_used=True on the first real run."""
    client = _FakeClient(_FakeResponse(url="https://acme.com/careers", text="<p>loading...</p>"))
    renderer = _FakeRenderer(
        pec.RenderResult(
            html="<div id='root'></div>",  # rendered DOM has no ATS link anywhere
            final_url="https://acme.com/careers",
            error=None,
            network_urls=["https://boards-api.greenhouse.io/v1/boards/acmeworks/jobs"],
        )
    )
    job = _job(company="Acme", description="y" * 400)
    fake = _fake_scraper_class(jobs=[job])
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "greenhouse", fake)

    employer = {"name": "Acme", "careers_url": "https://acme.com/careers", "targets": []}
    result = pec.probe_employer(client, employer, sample_size=8, fetch_timeout=10, renderer=renderer)

    assert result.status == "probed"
    assert result.adapter == "greenhouse"
    assert result.endpoint_config == "acmeworks"
    assert result.detection_method == "js_render"
    assert result.verified is True


def test_probe_employer_never_renders_when_static_already_found_something(monkeypatch):
    html = '<a href="https://boards.greenhouse.io/acme/jobs/1">Careers</a>'
    client = _FakeClient(_FakeResponse(url="https://acme.com/careers", text=html))
    renderer = _FakeRenderer(pec.RenderResult(html="", final_url=None, error=None))
    job = _job(company="Acme", description="y" * 400)
    fake = _fake_scraper_class(jobs=[job])
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "greenhouse", fake)

    employer = {"name": "Acme", "careers_url": "https://acme.com/careers", "targets": []}
    result = pec.probe_employer(client, employer, sample_size=8, fetch_timeout=10, renderer=renderer)

    assert renderer.calls == []
    assert result.detection_method == "static"
    assert result.render_used is False


def test_probe_employer_records_render_error_without_crashing(monkeypatch):
    client = _FakeClient(_FakeResponse(url="https://acme.com/careers", text="<p>loading...</p>"))
    renderer = _FakeRenderer(
        pec.RenderResult(html="", final_url=None, error="playwright not installed: no module named 'playwright'")
    )
    employer = {"name": "Acme", "careers_url": "https://acme.com/careers", "targets": []}

    result = pec.probe_employer(client, employer, sample_size=8, fetch_timeout=10, renderer=renderer)

    assert result.status == "adapter_not_detected"
    assert result.render_used is False
    assert result.render_error is not None
    assert "playwright not installed" in result.render_error


def test_probe_employer_render_finding_nothing_is_still_adapter_not_detected(monkeypatch):
    """The renderer running successfully but finding no ATS link is a
    different, more confident signal than 'render unavailable' -- both end
    in adapter_not_detected, but render_used distinguishes them in the
    audit."""
    client = _FakeClient(_FakeResponse(url="https://acme.com/careers", text="<p>loading...</p>"))
    renderer = _FakeRenderer(
        pec.RenderResult(html="<p>Bespoke internal tracker, no ATS link anywhere</p>", final_url="https://acme.com/careers", error=None)
    )
    employer = {"name": "Acme", "careers_url": "https://acme.com/careers", "targets": []}

    result = pec.probe_employer(client, employer, sample_size=8, fetch_timeout=10, renderer=renderer)

    assert result.status == "adapter_not_detected"
    assert result.render_used is True
    assert result.render_error is None


def test_probe_employer_without_a_renderer_skips_fallback_entirely():
    """No renderer supplied (e.g. --no-js-render) -- behaves exactly like
    before the fallback existed."""
    client = _FakeClient(_FakeResponse(url="https://acme.com/careers", text="<p>Custom ATS</p>"))
    employer = {"name": "Acme", "careers_url": "https://acme.com/careers", "targets": []}

    result = pec.probe_employer(client, employer, sample_size=8, fetch_timeout=10, renderer=None)

    assert result.status == "adapter_not_detected"
    assert result.render_used is False
    assert result.render_error is None


def test_probe_employer_records_fetch_failure_without_raising():
    client = _FakeClient(exc=httpx.ConnectTimeout("timed out"))
    employer = {"name": "Acme", "careers_url": "https://acme.com/careers", "targets": []}

    result = pec.probe_employer(client, employer, sample_size=8, fetch_timeout=10)

    assert result.status == "fetch_failed"
    assert result.verified is False
    assert result.error is not None


def test_probe_employer_marks_unverified_when_a_check_fails(monkeypatch):
    html = '<a href="https://boards.greenhouse.io/acme/jobs/1">Careers</a>'
    client = _FakeClient(_FakeResponse(url="https://acme.com/careers", text=html))
    job = _job(company="Acme", description=None)  # fails has_full_description
    fake = _fake_scraper_class(jobs=[job])
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "greenhouse", fake)

    employer = {"name": "Acme", "careers_url": "https://acme.com/careers", "targets": []}
    result = pec.probe_employer(client, employer, sample_size=8, fetch_timeout=10)

    assert result.status == "probed"
    assert result.verified is False
    assert result.checks["has_full_description"] is False


def test_probe_employer_records_scraper_failure_as_unverified_not_probed_silently(monkeypatch):
    html = '<a href="https://boards.greenhouse.io/acme/jobs/1">Careers</a>'
    client = _FakeClient(_FakeResponse(url="https://acme.com/careers", text=html))
    fake = _fake_scraper_class(error=ScraperError("HTTP 403"))
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "greenhouse", fake)

    employer = {"name": "Acme", "careers_url": "https://acme.com/careers", "targets": []}
    result = pec.probe_employer(client, employer, sample_size=8, fetch_timeout=10)

    assert result.status == "probed"
    assert result.verified is False
    assert "403" in result.error


def test_probe_employer_slices_sample_to_requested_size(monkeypatch):
    html = '<a href="https://boards.greenhouse.io/acme/jobs/1">Careers</a>'
    client = _FakeClient(_FakeResponse(url="https://acme.com/careers", text=html))
    jobs = [
        _job(source_id=str(i), company="Acme", description="x" * 400) for i in range(20)
    ]
    fake = _fake_scraper_class(jobs=jobs)
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "greenhouse", fake)

    employer = {"name": "Acme", "careers_url": "https://acme.com/careers", "targets": []}
    result = pec.probe_employer(client, employer, sample_size=3, fetch_timeout=10)

    assert result.sample_size == 3


# ---------------------------------------------------------------------------
# run_probe -- artifact writing (still no network)
# ---------------------------------------------------------------------------


def _cohort_yaml(*entries: str) -> str:
    return "employers:\n" + "".join(f"  - {entry}\n" for entry in entries)


def test_run_probe_writes_three_artifacts_and_never_enables_anything(tmp_path, monkeypatch):
    cohort_path = tmp_path / "cohort.yaml"
    cohort_path.write_text(
        _cohort_yaml(
            "{name: Acme, careers_url: 'https://acme.com/careers', evidence_date: 2026-07-12, "
            "rationale: test, targets: [ai-implementation], enabled: false, verification_status: pending}",
            "{name: BrokenCo, careers_url: 'https://broken.example/careers', evidence_date: 2026-07-12, "
            "rationale: test, targets: [ai-implementation], enabled: false, verification_status: pending}",
        ),
        encoding="utf-8",
    )
    html = '<a href="https://boards.greenhouse.io/acme/jobs/1">Careers</a>'
    good_job = _job(company="Acme", description="z" * 400)
    fake = _fake_scraper_class(jobs=[good_job])
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "greenhouse", fake)

    responses = {
        "https://acme.com/careers": _FakeResponse(url="https://acme.com/careers", text=html),
        "https://broken.example/careers": _FakeResponse(
            url="https://broken.example/careers", text="<p>nothing here</p>"
        ),
    }

    class _SequencedClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def get(self, url, timeout=None):
            return responses[url]

    monkeypatch.setattr(pec.httpx, "Client", _SequencedClient)

    out_dir = tmp_path / "audits"
    # use_js_render=False -- BrokenCo's static detection legitimately finds
    # nothing, and this test must stay pure-Python/no-network; a default
    # True here would try to launch a real headless Chromium.
    summary = pec.run_probe(
        cohort_path=cohort_path, out_dir=out_dir, only=None, sample_size=8, fetch_timeout=10, use_js_render=False
    )

    assert summary["verified_count"] == 1
    assert summary["failed_count"] == 1
    assert summary["verified_employers"] == ["Acme"]
    assert summary["js_render_fallback_used_count"] == 0
    # render_error must be in the console summary, not just the full audit
    # JSON -- a run that fails on a render timeout needs that visible
    # without digging through data/audits/employer_cohort_probe.json.
    assert "render_error" in summary["failed_employers"][0]

    audit_path = out_dir / "employer_cohort_probe.json"
    companies_patch_path = out_dir / "employer_cohort_probe.proposed_companies_patch.yaml"
    cohort_patch_path = out_dir / "employer_cohort_probe.proposed_cohort_status.yaml"
    assert audit_path.exists()
    assert companies_patch_path.exists()
    assert cohort_patch_path.exists()

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["total_employers"] == 2
    # Failures are retained for review, not dropped (req #7).
    assert len(audit["results"]) == 2
    assert {r["name"] for r in audit["results"]} == {"Acme", "BrokenCo"}

    companies_patch = yaml.safe_load(companies_patch_path.read_text(encoding="utf-8"))
    assert companies_patch == {"greenhouse": ["acme"]}

    cohort_patch = yaml.safe_load(cohort_patch_path.read_text(encoding="utf-8"))
    assert cohort_patch["Acme"]["verification_status"] == "verified_probe_passed"
    assert cohort_patch["Acme"]["enabled"] is False
    assert cohort_patch["BrokenCo"]["verification_status"] == "probe_failed"
    assert cohort_patch["BrokenCo"]["enabled"] is False
    # Never silently proposes the human-only approved status or flips enabled --
    # scripts/validate_employer_cohort.py's release gate depends on this.
    for entry in cohort_patch.values():
        assert entry["verification_status"] != "verified_approved"
        assert entry["enabled"] is False


def test_run_probe_respects_only_filter(tmp_path, monkeypatch):
    cohort_path = tmp_path / "cohort.yaml"
    cohort_path.write_text(
        _cohort_yaml(
            "{name: Acme, careers_url: 'https://acme.com/careers', evidence_date: 2026-07-12, "
            "rationale: test, targets: [], enabled: false, verification_status: pending}",
            "{name: Other, careers_url: 'https://other.example/careers', evidence_date: 2026-07-12, "
            "rationale: test, targets: [], enabled: false, verification_status: pending}",
        ),
        encoding="utf-8",
    )

    class _EmptyClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def get(self, url, timeout=None):
            return _FakeResponse(url=url, text="<p>nothing</p>")

    monkeypatch.setattr(pec.httpx, "Client", _EmptyClient)

    out_dir = tmp_path / "audits"
    summary = pec.run_probe(
        cohort_path=cohort_path,
        out_dir=out_dir,
        only={"Acme"},
        sample_size=8,
        fetch_timeout=10,
        use_js_render=False,
    )

    assert summary["total_employers"] == 1


def test_run_probe_wires_renderer_through_when_js_render_enabled(tmp_path, monkeypatch):
    """Confirms run_probe actually constructs and passes down a renderer
    (rather than only probe_employer's unit tests exercising the fallback
    in isolation), and that it's cleaned up via the `with` context -- using
    a fake BrowserRenderer so this stays pure-Python/no-network even with
    use_js_render=True."""
    cohort_path = tmp_path / "cohort.yaml"
    cohort_path.write_text(
        _cohort_yaml(
            "{name: Acme, careers_url: 'https://acme.com/careers', evidence_date: 2026-07-12, "
            "rationale: test, targets: [], enabled: false, verification_status: pending}",
        ),
        encoding="utf-8",
    )

    class _StaticOnlyClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def get(self, url, timeout=None):
            return _FakeResponse(url=url, text="<p>loading...</p>")

    monkeypatch.setattr(pec.httpx, "Client", _StaticOnlyClient)

    job = _job(company="Acme", description="z" * 400)
    fake_scraper = _fake_scraper_class(jobs=[job])
    monkeypatch.setitem(pec._SCRAPER_CLASSES, "greenhouse", fake_scraper)

    instances: list[object] = []

    class _FakeBrowserRenderer:
        def __init__(self, *args, **kwargs):
            self.closed = False
            instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.closed = True
            return False

        def render(self, url):
            return pec.RenderResult(
                html='<a href="https://boards.greenhouse.io/acme/jobs/1">Careers</a>',
                final_url=url,
                error=None,
            )

    monkeypatch.setattr(pec, "BrowserRenderer", _FakeBrowserRenderer)

    out_dir = tmp_path / "audits"
    summary = pec.run_probe(
        cohort_path=cohort_path, out_dir=out_dir, only=None, sample_size=8, fetch_timeout=10, use_js_render=True
    )

    assert summary["verified_count"] == 1
    assert summary["js_render_fallback_used_count"] == 1
    assert instances[0].closed is True  # cleanup ran via `with renderer`
