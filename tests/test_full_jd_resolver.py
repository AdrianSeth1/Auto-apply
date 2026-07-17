"""Tests for src.intake.full_jd_resolver (Phase S4 / SUP-07).

Pure Python: httpx is faked at the ``httpx.Client`` boundary used by
``_follow_application_url``, and the three reusable scraper classes
(Greenhouse/Lever/Ashby) are faked by monkeypatching the names imported
into ``src.intake.full_jd_resolver`` -- the same pattern already used by
``tests/test_probe_employer_cohort.py`` and
``tests/test_intake_endpoint_metrics.py`` in this codebase. No network, no
DB.
"""

from __future__ import annotations

import httpx

import src.intake.full_jd_resolver as fjr
from src.intake.base import ScraperError
from src.intake.schema import RawJob

ENABLED_POLICY = {
    "adapters": {
        "greenhouse": {"enabled": True},
        "lever": {"enabled": True},
        "ashby": {"enabled": True},
        "smartrecruiters": {"enabled": False},
        "workable": {"enabled": False},
        "recruitee": {"enabled": False},
    }
}


def _snippet_job(
    *, application_url: str = "https://example.com/redirect/123", **overrides
) -> RawJob:
    fields = {
        "source": "adzuna",
        "source_id": "adz-1",
        "company": "Acme Corp",
        "title": "Solutions Engineer",
        "description": "Short snippet...",
        "application_url": application_url,
        "raw_data": {"description_completeness": "snippet"},
    }
    fields.update(overrides)
    return RawJob(**fields)


class _FakeResponse:
    def __init__(self, url: str, text: str = "<html></html>"):
        self.url = url
        self.text = text


class _FakeHttpxClient:
    """Stand-in for httpx.Client used as a context manager."""

    def __init__(self, *, resolved_url: str | None = None, raise_error: bool = False, **_kwargs):
        self._resolved_url = resolved_url
        self._raise_error = raise_error

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, url):
        if self._raise_error:
            raise httpx.ConnectError("boom", request=httpx.Request("GET", url))
        return _FakeResponse(self._resolved_url or url)


def _patch_httpx_client(monkeypatch, *, resolved_url: str | None = None, raise_error: bool = False):
    def _factory(*args, **kwargs):
        return _FakeHttpxClient(resolved_url=resolved_url, raise_error=raise_error)

    monkeypatch.setattr(fjr.httpx, "Client", _factory)


# -- guard clauses that never touch the network -----------------------------


def test_non_adzuna_source_is_not_a_snippet_posting(monkeypatch):
    job = _snippet_job(source="greenhouse", ats_type="greenhouse")
    outcome = fjr.resolve_full_jd(job, source_policy=ENABLED_POLICY)
    assert outcome.resolved is False
    assert outcome.reason == "not_a_snippet_posting"


def test_non_snippet_completeness_is_not_a_snippet_posting():
    job = _snippet_job(raw_data={"description_completeness": "full"})
    outcome = fjr.resolve_full_jd(job, source_policy=ENABLED_POLICY)
    assert outcome.resolved is False
    assert outcome.reason == "not_a_snippet_posting"


def test_already_recovered_is_skipped():
    job = _snippet_job(raw_data={"description_completeness": "snippet", "full_jd_recovered": True})
    outcome = fjr.resolve_full_jd(job, source_policy=ENABLED_POLICY)
    assert outcome.resolved is False
    assert outcome.reason == "already_recovered"


def test_missing_application_url_is_skipped():
    job = _snippet_job(application_url=None)
    outcome = fjr.resolve_full_jd(job, source_policy=ENABLED_POLICY)
    assert outcome.resolved is False
    assert outcome.reason == "missing_application_url"


# -- redirect-follow stage ----------------------------------------------------


def test_redirect_follow_failure_is_reported_not_raised(monkeypatch):
    _patch_httpx_client(monkeypatch, raise_error=True)
    job = _snippet_job()
    outcome = fjr.resolve_full_jd(job, source_policy=ENABLED_POLICY)
    assert outcome.resolved is False
    assert outcome.reason == "redirect_follow_failed"


def test_unrecognized_resolved_url_is_left_alone(monkeypatch):
    _patch_httpx_client(monkeypatch, resolved_url="https://acmecorp.com/careers/some-job")
    job = _snippet_job()
    outcome = fjr.resolve_full_jd(job, source_policy=ENABLED_POLICY)
    assert outcome.resolved is False
    assert outcome.reason == "unrecognized_target"
    assert outcome.resolved_url == "https://acmecorp.com/careers/some-job"


# -- source_policy gating -----------------------------------------------------


def test_workable_url_is_recognized_but_not_refetchable(monkeypatch):
    # Workable is matched by _URL_PATTERNS (so the audit can say "this WAS
    # a Workable posting") but is not in _REFETCHABLE_ADAPTERS -- reusing
    # it would pre-empt Phase S3's still-pending conformance work
    # (SUP-05), so this must stop here regardless of source_policy.
    _patch_httpx_client(monkeypatch, resolved_url="https://apply.workable.com/acme/j/ABCDEF1234/")
    job = _snippet_job()
    outcome = fjr.resolve_full_jd(job, source_policy=ENABLED_POLICY)
    assert outcome.resolved is False
    assert outcome.reason == "adapter_not_refetchable"
    assert outcome.adapter == "workable"


def test_disabled_adapter_in_policy_is_rejected_without_calling_scraper(monkeypatch):
    _patch_httpx_client(monkeypatch, resolved_url="https://boards.greenhouse.io/acme/jobs/98765")

    class _ExplodingGreenhouse:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def fetch_job(self, *_a, **_kw):
            raise AssertionError("should never be called for a disabled adapter")

    monkeypatch.setattr(fjr, "GreenhouseScraper", _ExplodingGreenhouse)
    policy = {"adapters": {"greenhouse": {"enabled": False}}}
    job = _snippet_job()
    outcome = fjr.resolve_full_jd(job, source_policy=policy)
    assert outcome.resolved is False
    assert outcome.reason == "adapter_disabled_in_source_policy"
    assert outcome.adapter == "greenhouse"


def test_no_source_policy_defaults_to_fail_closed(monkeypatch):
    _patch_httpx_client(monkeypatch, resolved_url="https://boards.greenhouse.io/acme/jobs/98765")
    job = _snippet_job()
    outcome = fjr.resolve_full_jd(job)  # source_policy omitted entirely
    assert outcome.resolved is False
    assert outcome.reason == "adapter_disabled_in_source_policy"


def test_workday_style_url_is_unrecognized_not_attempted(monkeypatch):
    # Workday is intentionally absent from _URL_PATTERNS -- see module
    # docstring on why a static follow can't recover a Workday SPA anyway.
    _patch_httpx_client(
        monkeypatch,
        resolved_url="https://acme.wd1.myworkdayjobs.com/en-US/External/job/Remote/Engineer_R-1234",
    )
    job = _snippet_job()
    outcome = fjr.resolve_full_jd(job, source_policy=ENABLED_POLICY)
    assert outcome.resolved is False
    assert outcome.reason == "unrecognized_target"


# -- successful recovery through each reusable adapter ------------------------


def test_greenhouse_recovery_succeeds(monkeypatch):
    _patch_httpx_client(monkeypatch, resolved_url="https://boards.greenhouse.io/acme/jobs/98765")
    full_text = "A" * 400

    class _FakeGreenhouse:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def fetch_job(self, slug, job_id):
            assert slug == "acme"
            assert job_id == "98765"
            return RawJob(
                source="greenhouse",
                source_id=job_id,
                company="Acme Corp",
                title="Solutions Engineer",
                description=full_text,
                application_url="https://boards.greenhouse.io/acme/jobs/98765",
                ats_type="greenhouse",
            )

    monkeypatch.setattr(fjr, "GreenhouseScraper", _FakeGreenhouse)
    job = _snippet_job()
    outcome = fjr.resolve_full_jd(job, source_policy=ENABLED_POLICY)

    assert outcome.resolved is True
    assert outcome.adapter == "greenhouse"
    assert outcome.job.description == full_text
    assert outcome.job.raw_data["full_jd_recovered"] is True
    assert outcome.job.raw_data["description_completeness"] == "full"
    assert outcome.job.raw_data["full_jd_source_adapter"] == "greenhouse"
    assert outcome.job.provenance.application_target.resolution_status == "resolved_via_adapter"
    # Identity is preserved -- this enriches the SAME Adzuna posting.
    assert outcome.job.source == "adzuna"
    assert outcome.job.source_id == "adz-1"


def test_lever_recovery_succeeds_with_partial_length(monkeypatch):
    _patch_httpx_client(
        monkeypatch, resolved_url="https://jobs.lever.co/acme/1234abcd-5678-90ef-aaaa-bbbbccccdddd"
    )
    short_text = "B" * 150  # below the 300-char "full" threshold

    class _FakeLever:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def fetch_job(self, slug, job_id):
            assert slug == "acme"
            return RawJob(
                source="lever",
                source_id=job_id,
                company="Acme Corp",
                title="Solutions Engineer",
                description=short_text,
                application_url="https://jobs.lever.co/acme/" + job_id,
                ats_type="lever",
            )

    monkeypatch.setattr(fjr, "LeverScraper", _FakeLever)
    job = _snippet_job()
    outcome = fjr.resolve_full_jd(job, source_policy=ENABLED_POLICY)

    assert outcome.resolved is True
    assert outcome.job.raw_data["description_completeness"] == "partial"


def test_ashby_recovery_finds_matching_job_in_board(monkeypatch):
    _patch_httpx_client(
        monkeypatch,
        resolved_url="https://jobs.ashbyhq.com/acme/1234abcd-5678-90ef-aaaa-bbbbccccdddd",
    )
    full_text = "C" * 400

    class _FakeAshby:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def fetch_jobs(self, slug):
            assert slug == "acme"
            return [
                RawJob(
                    source="ashby",
                    source_id="other-id",
                    company="Acme",
                    title="Other role",
                    description="irrelevant",
                    ats_type="ashby",
                ),
                RawJob(
                    source="ashby",
                    source_id="1234abcd-5678-90ef-aaaa-bbbbccccdddd",
                    company="Acme",
                    title="Solutions Engineer",
                    description=full_text,
                    ats_type="ashby",
                ),
            ]

    monkeypatch.setattr(fjr, "AshbyScraper", _FakeAshby)
    job = _snippet_job()
    outcome = fjr.resolve_full_jd(job, source_policy=ENABLED_POLICY)

    assert outcome.resolved is True
    assert outcome.job.description == full_text


def test_ashby_job_id_not_found_in_board(monkeypatch):
    _patch_httpx_client(
        monkeypatch,
        resolved_url="https://jobs.ashbyhq.com/acme/1234abcd-5678-90ef-aaaa-bbbbccccdddd",
    )

    class _FakeAshby:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def fetch_jobs(self, slug):
            return []

    monkeypatch.setattr(fjr, "AshbyScraper", _FakeAshby)
    job = _snippet_job()
    outcome = fjr.resolve_full_jd(job, source_policy=ENABLED_POLICY)

    assert outcome.resolved is False
    assert outcome.reason == "job_not_found"


def test_scraper_error_is_reported_not_raised(monkeypatch):
    _patch_httpx_client(monkeypatch, resolved_url="https://boards.greenhouse.io/acme/jobs/98765")

    class _FailingGreenhouse:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def fetch_job(self, slug, job_id):
            raise ScraperError("HTTP 404 from boards-api.greenhouse.io")

    monkeypatch.setattr(fjr, "GreenhouseScraper", _FailingGreenhouse)
    job = _snippet_job()
    outcome = fjr.resolve_full_jd(job, source_policy=ENABLED_POLICY)

    assert outcome.resolved is False
    assert outcome.reason == "scraper_error"
    assert outcome.adapter == "greenhouse"


def test_recovered_empty_description_is_not_treated_as_success(monkeypatch):
    _patch_httpx_client(monkeypatch, resolved_url="https://boards.greenhouse.io/acme/jobs/98765")

    class _EmptyGreenhouse:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def fetch_job(self, slug, job_id):
            return RawJob(
                source="greenhouse",
                source_id=job_id,
                company="Acme",
                title="Solutions Engineer",
                description="   ",
                ats_type="greenhouse",
            )

    monkeypatch.setattr(fjr, "GreenhouseScraper", _EmptyGreenhouse)
    job = _snippet_job()
    outcome = fjr.resolve_full_jd(job, source_policy=ENABLED_POLICY)

    assert outcome.resolved is False
    assert outcome.reason == "recovered_description_empty"
