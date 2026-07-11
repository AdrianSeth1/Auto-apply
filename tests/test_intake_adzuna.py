"""Tests for the Adzuna intake adapter (src/intake/adzuna.py) and its
wiring into src.application.jobs.search_jobs (source leg, call cap,
board-discovery handoff).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.intake.adzuna import AdzunaScraper
from src.intake.base import ScraperError

LOCKHEED_JOB = {
    "id": "5773101570",
    "title": "Software Engineer with Poly SR SWE2",
    "company": {"display_name": "Lockheed Martin", "__CLASS__": "Adzuna::API::Response::Company"},
    "location": {
        "display_name": "Savage, Anne Arundel County",
        "area": ["US", "Maryland", "Anne Arundel County", "Savage"],
        "__CLASS__": "Adzuna::API::Response::Location",
    },
    "description": "This senior role fosters collaboration with other senior engineers…",
    "redirect_url": "https://www.adzuna.com/land/ad/5773101570?se=abc&utm_medium=api",
    "created": "2026-06-22T22:29:57Z",
    "salary_min": 203357.95,
    "salary_max": 203357.95,
    "salary_is_predicted": "1",
    "contract_time": "full_time",
    "category": {"label": "IT Jobs", "tag": "it-jobs"},
}

INTERN_JOB = {
    "id": "999",
    "title": "Software Engineering Intern",
    "company": {"display_name": "Acme Co"},
    "location": {"display_name": "Remote"},
    "description": "Join our team for the summer.",
    "redirect_url": "https://www.adzuna.com/land/ad/999",
    "created": "2026-07-01T00:00:00Z",
    "contract_time": "full_time",
}


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
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self._response

    def close(self) -> None:
        pass


def _scraper_with_response(
    monkeypatch: pytest.MonkeyPatch, payload: Any, *, status_code: int = 200
) -> tuple[AdzunaScraper, _FakeClient]:
    fake_client = _FakeClient(_FakeResponse(payload=payload, status_code=status_code))
    monkeypatch.setattr("src.intake.adzuna.httpx.Client", lambda **_kwargs: fake_client)
    scraper = AdzunaScraper(app_id="test-id", app_key="test-key", country="us")
    return scraper, fake_client


# ---- job mapping ------------------------------------------------------------


def test_search_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper, client = _scraper_with_response(monkeypatch, {"results": [LOCKHEED_JOB]})
    jobs = scraper.search(keyword="software engineer", location="Maryland")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "adzuna"
    assert job.ats_type == "unknown"
    assert job.source_id == "5773101570"
    assert job.company == "Lockheed Martin"
    assert job.title == "Software Engineer with Poly SR SWE2"
    assert job.location == "Savage, Anne Arundel County"
    assert job.employment_type == "fulltime"
    assert job.application_url == LOCKHEED_JOB["redirect_url"]
    assert job.raw_data == LOCKHEED_JOB
    # "created" preserved for the scorer's ghost-age check.
    assert job.raw_data["created"] == "2026-06-22T22:29:57Z"

    call = client.calls[0]
    assert call["url"] == "https://api.adzuna.com/v1/api/jobs/us/search/1"
    assert call["params"]["app_id"] == "test-id"
    assert call["params"]["app_key"] == "test-key"
    assert call["params"]["what"] == "software engineer"
    assert call["params"]["where"] == "Maryland"


def test_search_classifies_internship_from_title(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper, _ = _scraper_with_response(monkeypatch, {"results": [INTERN_JOB]})
    jobs = scraper.search(keyword="intern")

    assert jobs[0].employment_type == "internship"
    assert jobs[0].seniority == "internship"
    assert jobs[0].location == "Remote"


def test_search_missing_company_and_location_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    item = dict(LOCKHEED_JOB)
    item["company"] = {}
    item["location"] = {}
    scraper, _ = _scraper_with_response(monkeypatch, {"results": [item]})
    jobs = scraper.search(keyword="x")

    assert jobs[0].company == "Unknown"
    assert jobs[0].location is None


def test_posting_age_uses_created_field() -> None:
    from datetime import UTC, datetime, timedelta

    from src.intake.schema import RawJob
    from src.matching.scorer import _posting_age_days

    job = RawJob(
        source="adzuna",
        source_id="1",
        company="Acme",
        title="SWE",
        application_url="https://example.com",
        raw_data={"created": (datetime.now(UTC) - timedelta(days=10)).isoformat()},
    )
    age = _posting_age_days(job)
    assert age is not None and 9 <= age <= 11


# ---- malformed / error responses -------------------------------------------


def test_search_skips_malformed_job_item(monkeypatch: pytest.MonkeyPatch) -> None:
    malformed = {"title": "Missing id field"}
    scraper, _ = _scraper_with_response(monkeypatch, {"results": [malformed]})
    assert scraper.search(keyword="x") == []


def test_search_empty_results_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper, _ = _scraper_with_response(monkeypatch, {"results": []})
    assert scraper.search(keyword="x") == []


def test_search_raises_scraper_error_on_bad_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper, _ = _scraper_with_response(monkeypatch, {"results": "not-a-list"})
    with pytest.raises(ScraperError):
        scraper.search(keyword="x")


def test_search_raises_scraper_error_on_non_dict_response(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper, _ = _scraper_with_response(monkeypatch, ["not", "a", "dict"])
    with pytest.raises(ScraperError):
        scraper.search(keyword="x")


def test_search_raises_scraper_error_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper, _ = _scraper_with_response(monkeypatch, None, status_code=403)
    with pytest.raises(ScraperError):
        scraper.search(keyword="x")


def test_construction_requires_credentials() -> None:
    with pytest.raises(ScraperError):
        AdzunaScraper(app_id="", app_key="key")
    with pytest.raises(ScraperError):
        AdzunaScraper(app_id="id", app_key="")


# ---- board discovery handoff ------------------------------------------------


def test_board_discovery_extracts_slug_from_adzuna_job() -> None:
    """discover_board_slugs works on any RawJob regardless of source --
    if Adzuna ever returns a direct board URL (rather than its usual
    adzuna.com/land/ad/... tracking redirect) it feeds the same
    self-growing companies.yaml registry as LinkedIn.
    """
    from src.intake.board_discovery import discover_board_slugs
    from src.intake.schema import RawJob

    job = RawJob(
        source="adzuna",
        source_id="1",
        company="Vercel",
        title="Engineer",
        application_url="https://boards.greenhouse.io/vercel/jobs/123",
        raw_data={"redirect_url": "https://boards.greenhouse.io/vercel/jobs/123"},
    )
    found = discover_board_slugs([job])
    assert found["greenhouse"] == {"vercel"}


def test_board_discovery_noop_for_adzuna_tracking_redirect() -> None:
    """The realistic case: Adzuna's redirect_url is its own tracking
    landing page, which does not match any known ATS pattern. Discovery
    must be a harmless no-op, not an error.
    """
    from src.intake.board_discovery import discover_board_slugs
    from src.intake.schema import RawJob

    job = RawJob(
        source="adzuna",
        source_id="1",
        company="Lockheed Martin",
        title="Engineer",
        application_url="https://www.adzuna.com/land/ad/5773101570?se=abc",
        raw_data=LOCKHEED_JOB,
    )
    found = discover_board_slugs([job])
    assert found["greenhouse"] == set()
    assert found["lever"] == set()
    assert found["ashby"] == set()


# ---- application.jobs._search_adzuna wiring ---------------------------------


class _StubAdzunaScraper:
    """Records search() calls; returns one job per call so call-count
    assertions double as a result-count check."""

    instances: list[_StubAdzunaScraper] = []

    def __init__(self, *, app_id: str, app_key: str, country: str) -> None:
        self.app_id = app_id
        self.app_key = app_key
        self.country = country
        self.search_calls: list[dict[str, Any]] = []
        _StubAdzunaScraper.instances.append(self)

    def __enter__(self) -> _StubAdzunaScraper:
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass

    def search(self, *, keyword: str, location: str, results_per_page: int) -> list[Any]:
        self.search_calls.append(
            {"keyword": keyword, "location": location, "results_per_page": results_per_page}
        )
        from src.intake.schema import RawJob

        return [
            RawJob(
                source="adzuna",
                source_id=f"{keyword or 'broad'}-{len(self.search_calls)}",
                company=f"Co-{keyword}",
                title=f"Role for {keyword}",
                application_url="https://www.adzuna.com/land/ad/1",
                raw_data={},
            )
        ]


@pytest.fixture(autouse=True)
def _reset_stub_instances():
    _StubAdzunaScraper.instances = []
    yield
    _StubAdzunaScraper.instances = []


def _adzuna_config(**overrides) -> dict:
    config = {
        "enabled": True,
        "app_id": "test-id",
        "app_key_env": "AUTOAPPLY_ADZUNA_KEY",
        "country": "us",
        "results_per_query": 50,
    }
    config.update(overrides)
    return {"adzuna": config}


def test_search_adzuna_disabled_returns_no_jobs():
    from src.application.jobs import _search_adzuna

    with patch("src.application.jobs.load_config", return_value=_adzuna_config(enabled=False)):
        assert _search_adzuna(["swe"], "Remote") == []


def test_search_adzuna_missing_key_returns_no_jobs(monkeypatch: pytest.MonkeyPatch):
    from src.application.jobs import _search_adzuna

    monkeypatch.delenv("AUTOAPPLY_ADZUNA_KEY", raising=False)
    with patch("src.application.jobs.load_config", return_value=_adzuna_config()):
        assert _search_adzuna(["swe"], "Remote") == []


def test_search_adzuna_queries_once_per_keyword(monkeypatch: pytest.MonkeyPatch):
    from src.application import jobs as jobs_module

    monkeypatch.setenv("AUTOAPPLY_ADZUNA_KEY", "secret")
    monkeypatch.setattr("src.intake.adzuna.AdzunaScraper", _StubAdzunaScraper)
    with patch("src.application.jobs.load_config", return_value=_adzuna_config()):
        result = jobs_module._search_adzuna(["backend", "frontend"], "Remote")

    assert len(_StubAdzunaScraper.instances) == 1
    calls = _StubAdzunaScraper.instances[0].search_calls
    assert [c["keyword"] for c in calls] == ["backend", "frontend"]
    assert all(c["location"] == "Remote" for c in calls)
    assert len(result) == 2


def test_search_adzuna_caps_calls_at_ten(monkeypatch: pytest.MonkeyPatch):
    from src.application import jobs as jobs_module

    monkeypatch.setenv("AUTOAPPLY_ADZUNA_KEY", "secret")
    monkeypatch.setattr("src.intake.adzuna.AdzunaScraper", _StubAdzunaScraper)
    many_keywords = [f"kw{i}" for i in range(15)]
    with patch("src.application.jobs.load_config", return_value=_adzuna_config()):
        result = jobs_module._search_adzuna(many_keywords, "Remote")

    assert len(_StubAdzunaScraper.instances[0].search_calls) == 10
    assert len(result) == 10


def test_search_adzuna_no_keywords_makes_one_broad_call(monkeypatch: pytest.MonkeyPatch):
    from src.application import jobs as jobs_module

    monkeypatch.setenv("AUTOAPPLY_ADZUNA_KEY", "secret")
    monkeypatch.setattr("src.intake.adzuna.AdzunaScraper", _StubAdzunaScraper)
    with patch("src.application.jobs.load_config", return_value=_adzuna_config()):
        result = jobs_module._search_adzuna([], "Remote")

    assert len(_StubAdzunaScraper.instances[0].search_calls) == 1
    assert _StubAdzunaScraper.instances[0].search_calls[0]["keyword"] == ""
    assert len(result) == 1
