"""Tests for the Workday intake adapter (src/intake/workday.py), its
board-fetch wiring in src/intake/search.py, and the keyword-gated detail
enrichment in src/application/jobs.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.intake.base import ScraperError
from src.intake.workday import (
    DETAIL_FETCH_CAP,
    MAX_JOBS_PER_BOARD,
    PAGE_SIZE,
    WorkdayScraper,
    _parse_relative_posted,
)

TENANT_CONFIG = {"tenant": "salesforce", "host": "wd12", "site": "External_Career_Site"}


def _job_item(i: int, **overrides: Any) -> dict:
    item = {
        "title": f"Software Engineer {i}",
        "externalPath": f"/job/Remote/Software-Engineer-{i}_JR{i}",
        "locationsText": "Remote",
        "postedOn": "Posted Today",
        "bulletFields": [f"JR{i}"],
    }
    item.update(overrides)
    return item


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self  # type: ignore[arg-type]
            )

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    """Pops one queued response per call, in order -- works for both the
    paginated POST list calls and GET detail calls."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return self._responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(("GET", url, kwargs))
        return self._responses.pop(0)

    def close(self) -> None:
        pass


def _scraper_with_responses(
    monkeypatch: pytest.MonkeyPatch, responses: list[_FakeResponse]
) -> tuple[WorkdayScraper, _FakeClient]:
    fake_client = _FakeClient(responses)
    monkeypatch.setattr("src.intake.workday.httpx.Client", lambda **_kwargs: fake_client)
    return WorkdayScraper(), fake_client


# ---- job mapping ------------------------------------------------------------


def test_fetch_jobs_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"total": 1, "jobPostings": [_job_item(1)]}
    scraper, client = _scraper_with_responses(monkeypatch, [_FakeResponse(payload=payload)])
    jobs = scraper.fetch_jobs(TENANT_CONFIG)

    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "workday"
    assert job.ats_type == "workday"
    assert job.source_id == "/job/Remote/Software-Engineer-1_JR1"
    assert job.company == "Salesforce"
    assert job.title == "Software Engineer 1"
    assert job.location == "Remote"
    assert job.description is None  # list endpoint has no description at all
    assert job.application_url == (
        "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site"
        "/job/Remote/Software-Engineer-1_JR1"
    )
    assert job.raw_data["workday_detail_url"] == (
        "https://salesforce.wd12.myworkdayjobs.com/wday/cxs/salesforce"
        "/External_Career_Site/job/Remote/Software-Engineer-1_JR1"
    )
    assert job.raw_data["workday_tenant"] == "salesforce"
    assert job.raw_data["workday_posted_date"]  # "Posted Today" parsed

    call = client.calls[0]
    assert call[0] == "POST"
    assert call[1] == "https://salesforce.wd12.myworkdayjobs.com/wday/cxs/salesforce/External_Career_Site/jobs"
    assert call[2]["json"]["limit"] == PAGE_SIZE
    assert call[2]["json"]["offset"] == 0


def test_fetch_jobs_classifies_internship_despite_generic_bullet_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _job_item(2, title="Software Engineering Intern", bulletFields=["Regular", "JR2"])
    payload = {"total": 1, "jobPostings": [item]}
    scraper, _ = _scraper_with_responses(monkeypatch, [_FakeResponse(payload=payload)])
    jobs = scraper.fetch_jobs(TENANT_CONFIG)

    assert jobs[0].employment_type == "internship"
    assert jobs[0].seniority == "internship"


def test_fetch_jobs_locations_text_multi_location_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _job_item(3, locationsText="2 Locations")
    payload = {"total": 1, "jobPostings": [item]}
    scraper, _ = _scraper_with_responses(monkeypatch, [_FakeResponse(payload=payload)])
    jobs = scraper.fetch_jobs(TENANT_CONFIG)

    assert jobs[0].location == "2 Locations"


def test_fetch_jobs_skips_malformed_job_item(monkeypatch: pytest.MonkeyPatch) -> None:
    malformed = {"title": "Missing externalPath"}
    payload = {"total": 1, "jobPostings": [malformed, _job_item(1)]}
    scraper, _ = _scraper_with_responses(monkeypatch, [_FakeResponse(payload=payload)])
    jobs = scraper.fetch_jobs(TENANT_CONFIG)

    assert len(jobs) == 1
    assert jobs[0].source_id == "/job/Remote/Software-Engineer-1_JR1"


# ---- pagination --------------------------------------------------------------


def test_fetch_jobs_paginates_and_stops_on_short_page(monkeypatch: pytest.MonkeyPatch) -> None:
    page1 = {"total": 25, "jobPostings": [_job_item(i) for i in range(20)]}
    page2 = {"total": 0, "jobPostings": [_job_item(i) for i in range(20, 25)]}
    scraper, client = _scraper_with_responses(
        monkeypatch, [_FakeResponse(payload=page1), _FakeResponse(payload=page2)]
    )
    jobs = scraper.fetch_jobs(TENANT_CONFIG)

    assert len(jobs) == 25
    assert len(client.calls) == 2
    assert client.calls[0][2]["json"]["offset"] == 0
    assert client.calls[1][2]["json"]["offset"] == 20
    # Second page returned "total": 0 (observed live on real tenants) --
    # pagination must not trust "total" past the first page.


def test_fetch_jobs_caps_at_max_jobs_per_board(monkeypatch: pytest.MonkeyPatch) -> None:
    full_pages = [
        _FakeResponse(
            payload={
                "total": 10_000,
                "jobPostings": [_job_item(p * PAGE_SIZE + i) for i in range(PAGE_SIZE)],
            }
        )
        for p in range(MAX_JOBS_PER_BOARD // PAGE_SIZE)
    ]
    scraper, client = _scraper_with_responses(monkeypatch, full_pages)
    jobs = scraper.fetch_jobs(TENANT_CONFIG)

    assert len(jobs) == MAX_JOBS_PER_BOARD
    assert len(client.calls) == MAX_JOBS_PER_BOARD // PAGE_SIZE  # no extra page requested


def test_fetch_jobs_empty_first_page_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"total": 0, "jobPostings": []}
    scraper, _ = _scraper_with_responses(monkeypatch, [_FakeResponse(payload=payload)])
    assert scraper.fetch_jobs(TENANT_CONFIG) == []


# ---- bad tenant / error handling ---------------------------------------------


def test_fetch_jobs_raises_scraper_error_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper, _ = _scraper_with_responses(monkeypatch, [_FakeResponse(status_code=404)])
    with pytest.raises(ScraperError):
        scraper.fetch_jobs(TENANT_CONFIG)


def test_fetch_jobs_raises_scraper_error_on_workday_error_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong tenant/site often 200s with a Workday-native error body
    instead of a transport-level failure."""
    payload = {"errorCode": "HTTP_422", "httpStatus": 422}
    scraper, _ = _scraper_with_responses(monkeypatch, [_FakeResponse(payload=payload)])
    with pytest.raises(ScraperError):
        scraper.fetch_jobs(TENANT_CONFIG)


def test_fetch_jobs_raises_scraper_error_on_bad_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper, _ = _scraper_with_responses(monkeypatch, [_FakeResponse(payload={"unexpected": 1})])
    with pytest.raises(ScraperError):
        scraper.fetch_jobs(TENANT_CONFIG)


def test_fetch_jobs_raises_scraper_error_on_non_dict_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper, _ = _scraper_with_responses(monkeypatch, [_FakeResponse(payload=["not", "a", "dict"])])
    with pytest.raises(ScraperError):
        scraper.fetch_jobs(TENANT_CONFIG)


# ---- detail fetch -------------------------------------------------------------


def test_fetch_job_detail_updates_description_and_posted_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.intake.schema import RawJob

    job = RawJob(
        source="workday",
        source_id="/job/x/1",
        company="Salesforce",
        title="Engineer",
        application_url="https://salesforce.wd12.myworkdayjobs.com/External_Career_Site/job/x/1",
        raw_data={
            "workday_detail_url": (
                "https://salesforce.wd12.myworkdayjobs.com/wday/cxs/salesforce"
                "/External_Career_Site/job/x/1"
            ),
            "workday_posted_date": "2026-07-05",
        },
    )
    detail_payload = {
        "jobPostingInfo": {
            "jobDescription": "<p>Build things.</p>",
            "startDate": "2026-07-10",
        }
    }
    scraper, _ = _scraper_with_responses(monkeypatch, [_FakeResponse(payload=detail_payload)])
    updated = scraper.fetch_job_detail(job)

    assert updated.description == "Build things."
    assert updated.raw_data["workday_posted_date"] == "2026-07-10"  # more precise, overwrites
    assert job.description is None  # original untouched (model_copy)


def test_fetch_job_detail_returns_original_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.intake.schema import RawJob

    job = RawJob(
        source="workday",
        source_id="/job/x/1",
        company="Salesforce",
        title="Engineer",
        raw_data={"workday_detail_url": "https://salesforce.wd12.myworkdayjobs.com/wday/cxs/x"},
    )
    scraper, _ = _scraper_with_responses(monkeypatch, [_FakeResponse(status_code=500)])
    updated = scraper.fetch_job_detail(job)

    assert updated is job  # unchanged, no raise


def test_fetch_job_detail_no_url_returns_original() -> None:
    from src.intake.schema import RawJob

    job = RawJob(source="workday", source_id="/job/x/1", company="Salesforce", title="Engineer")
    scraper = WorkdayScraper()
    assert scraper.fetch_job_detail(job) is job


# ---- relative-date parsing -----------------------------------------------------


@pytest.mark.parametrize(
    "text,expected_days",
    [
        ("Posted Today", 0),
        ("Posted Yesterday", 1),
        ("Posted 3 Days Ago", 3),
        ("Posted 30+ Days Ago", 30),
    ],
)
def test_parse_relative_posted(text: str, expected_days: int) -> None:
    from datetime import UTC, datetime, timedelta

    result = _parse_relative_posted(text)
    expected = (datetime.now(UTC) - timedelta(days=expected_days)).date()
    assert result == expected.isoformat()


def test_parse_relative_posted_unrecognized_returns_none() -> None:
    assert _parse_relative_posted("Coming Soon") is None
    assert _parse_relative_posted(None) is None
    assert _parse_relative_posted("") is None


def test_posting_age_uses_workday_posted_date() -> None:
    from datetime import UTC, datetime, timedelta

    from src.intake.schema import RawJob
    from src.matching.scorer import _posting_age_days

    job = RawJob(
        source="workday",
        source_id="1",
        company="Acme",
        title="SWE",
        raw_data={
            "workday_posted_date": (datetime.now(UTC) - timedelta(days=15)).date().isoformat()
        },
    )
    age = _posting_age_days(job)
    assert age is not None and 14 <= age <= 16


# ---- companies.yaml malformed entries -----------------------------------------


def test_fetch_company_jobs_missing_key_raises_keyerror(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch_company_jobs propagates a KeyError for a malformed tenant
    config -- the board loop in src.intake.search is what catches this
    and self-prunes (tested separately below)."""
    from src.intake.workday import fetch_company_jobs

    with pytest.raises(KeyError):
        fetch_company_jobs({"tenant": "salesforce", "host": "wd12"})  # missing "site"


# ---- src.intake.search board-loop wiring --------------------------------------


class _StubWorkdayScraper:
    fetch_calls: list[dict] = []

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> _StubWorkdayScraper:
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass

    def fetch_jobs(self, tenant_config: dict) -> list[Any]:
        _StubWorkdayScraper.fetch_calls.append(tenant_config)
        from src.intake.schema import RawJob

        if tenant_config["tenant"] == "badtenant":
            raise ScraperError("bad tenant")
        return [
            RawJob(
                source="workday",
                source_id=f"/job/{tenant_config['tenant']}/1",
                company=tenant_config["tenant"].title(),
                title="Engineer",
                application_url="https://example.com",
                raw_data={"workday_tenant": tenant_config["tenant"]},
            )
        ]


@pytest.fixture(autouse=True)
def _reset_stub_workday():
    _StubWorkdayScraper.fetch_calls = []
    yield
    _StubWorkdayScraper.fetch_calls = []


def test_search_jobs_board_loop_fetches_workday_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.intake.search import clear_board_cache, search_jobs

    clear_board_cache()
    monkeypatch.setattr("src.intake.search.WorkdayScraper", _StubWorkdayScraper)
    jobs = search_jobs(
        profile=None,
        companies={"workday": [dict(TENANT_CONFIG)]},
        parse_jds=False,
    )

    assert len(jobs) == 1
    assert jobs[0].source == "workday"
    assert _StubWorkdayScraper.fetch_calls == [TENANT_CONFIG]


def test_search_jobs_board_loop_skips_malformed_workday_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.intake.search import clear_board_cache, search_jobs

    clear_board_cache()
    monkeypatch.setattr("src.intake.search.WorkdayScraper", _StubWorkdayScraper)
    # Missing "site" -- must log a warning and skip, not crash the search.
    jobs = search_jobs(
        profile=None,
        companies={"workday": [{"tenant": "salesforce", "host": "wd12"}]},
        parse_jds=False,
    )

    assert jobs == []
    assert _StubWorkdayScraper.fetch_calls == []


def test_search_jobs_board_loop_bad_tenant_logs_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.intake.search import clear_board_cache, search_jobs

    clear_board_cache()
    monkeypatch.setattr("src.intake.search.WorkdayScraper", _StubWorkdayScraper)
    jobs = search_jobs(
        profile=None,
        companies={
            "workday": [
                {"tenant": "badtenant", "host": "wd1", "site": "External"},
                dict(TENANT_CONFIG),
            ]
        },
        parse_jds=False,
    )

    # Bad tenant contributes nothing; the good tenant's job still comes through.
    assert len(jobs) == 1
    assert jobs[0].raw_data["workday_tenant"] == "salesforce"


def test_slug_label() -> None:
    from src.intake.search import _slug_label

    assert _slug_label("stripe") == "stripe"
    assert _slug_label(("salesforce", "wd12", "External_Career_Site")) == "salesforce"


# ---- src.application.jobs._enrich_workday_job_details -------------------------


class _StubDetailWorkdayScraper:
    calls: list[str] = []

    def __enter__(self) -> _StubDetailWorkdayScraper:
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass

    def fetch_job_detail(self, job: Any) -> Any:
        _StubDetailWorkdayScraper.calls.append(job.source_id)
        return job.model_copy(update={"description": f"detail:{job.source_id}"})


@pytest.fixture(autouse=True)
def _reset_stub_detail_workday():
    _StubDetailWorkdayScraper.calls = []
    yield
    _StubDetailWorkdayScraper.calls = []


def _workday_job(source_id: str, tenant: str = "salesforce"):
    from src.intake.schema import RawJob

    return RawJob(
        source="workday",
        source_id=source_id,
        company=tenant.title(),
        title="Engineer",
        application_url="https://example.com",
        raw_data={"workday_tenant": tenant},
    )


def test_enrich_workday_job_details_skips_non_workday_jobs(monkeypatch: pytest.MonkeyPatch):
    from src.application.jobs import _enrich_workday_job_details
    from src.intake.schema import RawJob

    monkeypatch.setattr("src.intake.workday.WorkdayScraper", _StubDetailWorkdayScraper)
    gh_job = RawJob(source="greenhouse", source_id="1", company="Acme", title="Engineer")
    jobs = [gh_job]
    _enrich_workday_job_details(jobs)

    assert _StubDetailWorkdayScraper.calls == []
    assert jobs[0] is gh_job


def test_enrich_workday_job_details_enriches_all_under_cap(monkeypatch: pytest.MonkeyPatch):
    from src.application.jobs import _enrich_workday_job_details

    monkeypatch.setattr("src.intake.workday.WorkdayScraper", _StubDetailWorkdayScraper)
    jobs = [_workday_job(f"j{i}") for i in range(5)]
    _enrich_workday_job_details(jobs)

    assert sorted(_StubDetailWorkdayScraper.calls) == [f"j{i}" for i in range(5)]
    assert all(job.description == f"detail:{job.source_id}" for job in jobs)


def test_enrich_workday_job_details_caps_per_tenant(monkeypatch: pytest.MonkeyPatch):
    from src.application.jobs import _enrich_workday_job_details

    monkeypatch.setattr("src.intake.workday.WorkdayScraper", _StubDetailWorkdayScraper)
    jobs = [_workday_job(f"j{i}", tenant="salesforce") for i in range(DETAIL_FETCH_CAP + 5)]
    _enrich_workday_job_details(jobs)

    assert len(_StubDetailWorkdayScraper.calls) == DETAIL_FETCH_CAP
    enriched = [job for job in jobs if job.description]
    assert len(enriched) == DETAIL_FETCH_CAP


def test_enrich_workday_job_details_caps_independently_per_tenant(
    monkeypatch: pytest.MonkeyPatch,
):
    from src.application.jobs import _enrich_workday_job_details

    monkeypatch.setattr("src.intake.workday.WorkdayScraper", _StubDetailWorkdayScraper)
    jobs = [_workday_job(f"a{i}", tenant="tenant-a") for i in range(DETAIL_FETCH_CAP)]
    jobs += [_workday_job(f"b{i}", tenant="tenant-b") for i in range(DETAIL_FETCH_CAP)]
    _enrich_workday_job_details(jobs)

    # Each tenant gets its own cap -- both fully enriched, not shared.
    assert len(_StubDetailWorkdayScraper.calls) == DETAIL_FETCH_CAP * 2
    assert all(job.description for job in jobs)
