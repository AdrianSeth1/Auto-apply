"""Tests for the Remotive intake adapter (src/intake/remotive.py) and its
wiring into src.application.jobs.search_jobs (remote-only gating,
keyword narrowing).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.intake.base import ScraperError
from src.intake.remotive import RemotiveScraper

REMOTIVE_JOB = {
    "id": 2091048,
    "url": "https://remotive.com/remote-jobs/sales/product-sales-specialist-pet-health-2091048",
    "title": "Product Sales Specialist - Pet Health",
    "company_name": "Tribe Wellness",
    "company_logo": "https://remotive.com/job/2091048/logo",
    "category": "Sales",
    "tags": ["excel", "salesforce"],
    "job_type": "full_time",
    "publication_date": "2026-07-09T08:01:56",
    "candidate_required_location": "USA, CST (UTC-6)",
    "salary": "$55k - $100k",
    "description": "<p><strong>Must Love Dogs!!</strong></p><p>We build pet wellness tools.</p>",
}

REMOTIVE_INTERN_JOB = {
    "id": 999,
    "url": "https://remotive.com/remote-jobs/dev/backend-intern-999",
    "title": "Backend Engineering Intern",
    "company_name": "Acme Remote Co",
    "job_type": "full_time",
    "publication_date": "2026-07-01T00:00:00",
    "candidate_required_location": "Worldwide",
    "description": "Join our small remote team for the summer.",
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
) -> tuple[RemotiveScraper, _FakeClient]:
    fake_client = _FakeClient(_FakeResponse(payload=payload, status_code=status_code))
    monkeypatch.setattr("src.intake.base.httpx.Client", lambda **_kwargs: fake_client)
    return RemotiveScraper(), fake_client


# ---- job mapping ------------------------------------------------------------


def test_fetch_jobs_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper, client = _scraper_with_response(
        monkeypatch, {"job-count": 1, "total-job-count": 1, "jobs": [REMOTIVE_JOB]}
    )
    jobs = scraper.fetch_jobs("pet health")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "remotive"
    assert job.ats_type == "unknown"
    assert job.source_id == "2091048"
    assert job.company == "Tribe Wellness"
    assert job.title == "Product Sales Specialist - Pet Health"
    assert job.location == "USA, CST (UTC-6)"
    assert job.employment_type == "fulltime"
    assert job.application_url == REMOTIVE_JOB["url"]
    assert "Must Love Dogs" in job.description
    assert job.raw_data == REMOTIVE_JOB
    assert job.raw_data["publication_date"] == "2026-07-09T08:01:56"

    call = client.calls[0]
    assert call["params"] == {"search": "pet health"}


def test_fetch_jobs_empty_keyword_omits_search_param(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper, client = _scraper_with_response(monkeypatch, {"jobs": [REMOTIVE_JOB]})
    scraper.fetch_jobs("")

    assert client.calls[0]["params"] == {}


def test_fetch_jobs_classifies_internship(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper, _ = _scraper_with_response(monkeypatch, {"jobs": [REMOTIVE_INTERN_JOB]})
    jobs = scraper.fetch_jobs("intern")

    assert jobs[0].employment_type == "internship"
    assert jobs[0].seniority == "internship"
    assert jobs[0].location == "Worldwide"


# ---- malformed / error responses -------------------------------------------


def test_fetch_jobs_skips_malformed_job_item(monkeypatch: pytest.MonkeyPatch) -> None:
    malformed = {"title": "Missing id field"}
    scraper, _ = _scraper_with_response(monkeypatch, {"jobs": [malformed]})
    assert scraper.fetch_jobs("x") == []


def test_fetch_jobs_empty_jobs_list_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper, _ = _scraper_with_response(monkeypatch, {"jobs": []})
    assert scraper.fetch_jobs("x") == []


def test_fetch_jobs_raises_scraper_error_on_bad_top_level_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper, _ = _scraper_with_response(monkeypatch, {"jobs": "not-a-list"})
    with pytest.raises(ScraperError):
        scraper.fetch_jobs("x")


def test_fetch_jobs_raises_scraper_error_on_non_dict_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper, _ = _scraper_with_response(monkeypatch, ["not", "a", "dict"])
    with pytest.raises(ScraperError):
        scraper.fetch_jobs("x")


def test_fetch_jobs_raises_scraper_error_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper, _ = _scraper_with_response(monkeypatch, None, status_code=500)
    with pytest.raises(ScraperError):
        scraper.fetch_jobs("x")


# ---- ghost-age scorer integration -------------------------------------------


def test_posting_age_uses_publication_date() -> None:
    from datetime import UTC, datetime, timedelta

    from src.intake.schema import RawJob
    from src.matching.scorer import _posting_age_days

    naive_date = (datetime.now(UTC) - timedelta(days=8)).replace(tzinfo=None).isoformat()
    job = RawJob(
        source="remotive",
        source_id="1",
        company="Acme",
        title="SWE",
        raw_data={"publication_date": naive_date},
    )
    age = _posting_age_days(job)
    assert age is not None and 7 <= age <= 9


# ---- src.application.jobs wiring --------------------------------------------


def test_search_remotive_helper_passes_keyword():
    from src.application.jobs import _search_remotive

    with patch("src.intake.remotive.RemotiveScraper") as mock_cls:
        mock_scraper = mock_cls.return_value.__enter__.return_value
        mock_scraper.fetch_jobs.return_value = ["job1"]
        result = _search_remotive("backend")

    mock_scraper.fetch_jobs.assert_called_once_with("backend")
    assert result == ["job1"]


def test_search_jobs_only_fetches_remotive_when_remote_in_location_types():
    import asyncio

    with patch("src.application.jobs._search_remotive") as mock_search:
        mock_search.return_value = []
        from src.application.jobs import search_jobs

        asyncio.run(
            search_jobs(
                profile=None,
                source="all",
                keyword="backend",
                location_types=["hybrid", "in_person"],
                score=False,
                no_parse=True,
            )
        )

    mock_search.assert_not_called()


def test_search_jobs_fetches_remotive_when_remote_in_location_types():
    import asyncio

    from src.intake.schema import RawJob

    backend_job = RawJob(source="remotive", source_id="1", company="Acme", title="Backend Engineer")
    frontend_job = RawJob(
        source="remotive", source_id="2", company="Beta", title="Frontend Designer"
    )

    with patch("src.application.jobs._search_remotive", return_value=[backend_job, frontend_job]):
        from src.application.jobs import search_jobs

        result = asyncio.run(
            search_jobs(
                profile=None,
                source="all",
                keyword="backend",
                location_types=["remote"],
                score=False,
                no_parse=True,
            )
        )

    remotive_titles = [j["title"] for j in result["jobs"] if j["source"] == "remotive"]
    assert remotive_titles == ["Backend Engineer"]
    assert result["counts"]["remotive"] == 1


def test_search_jobs_remotive_leg_error_is_isolated():
    import asyncio

    with patch("src.application.jobs._search_remotive", side_effect=ScraperError("boom")):
        from src.application.jobs import search_jobs

        result = asyncio.run(
            search_jobs(
                profile=None,
                source="all",
                keyword="backend",
                location_types=["remote"],
                score=False,
                no_parse=True,
            )
        )

    assert any("Remotive" in err for err in result["errors"])
