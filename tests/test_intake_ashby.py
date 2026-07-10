"""Tests for the Ashby ATS scraper (src/intake/ashby.py) and its
board-discovery wiring.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.intake.ashby import AshbyScraper
from src.intake.base import ScraperError


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = ""

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


def _scraper_with_response(monkeypatch: pytest.MonkeyPatch, payload: Any) -> AshbyScraper:
    fake_client = _FakeClient(_FakeResponse(payload=payload))
    monkeypatch.setattr(
        "src.intake.base.httpx.Client", lambda **_kwargs: fake_client
    )
    return AshbyScraper()


NOTION_JOB = {
    "id": "03143d98-a561-44c6-96a5-fc9bfaf8d18d",
    "title": "Senior Software Engineer",
    "department": "Engineering",
    "team": "Platform",
    "employmentType": "FullTime",
    "location": "San Francisco, California",
    "secondaryLocations": [],
    "publishedAt": "2026-06-26T04:29:23.224+00:00",
    "isListed": True,
    "isRemote": None,
    "workplaceType": None,
    "jobUrl": "https://jobs.ashbyhq.com/notion/03143d98-a561-44c6-96a5-fc9bfaf8d18d",
    "applyUrl": "https://jobs.ashbyhq.com/notion/03143d98-a561-44c6-96a5-fc9bfaf8d18d/application",
    "descriptionPlain": "We are looking for a Senior Software Engineer with 5+ years experience.",
    "descriptionHtml": "<p>We are looking for a Senior Software Engineer.</p>",
}

REMOTE_INTERN_JOB = {
    "id": "abc-123",
    "title": "Software Engineering Intern",
    "employmentType": "Intern",
    "location": "New York, New York",
    "isRemote": True,
    "workplaceType": "Remote",
    "jobUrl": "https://jobs.ashbyhq.com/notion/abc-123",
    "applyUrl": "",
    "descriptionPlain": "Join our team as an intern for the summer.",
    "descriptionHtml": "",
}


# ---- job mapping ----------------------------------------------------------


def test_fetch_jobs_maps_employment_type_and_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper = _scraper_with_response(monkeypatch, {"jobs": [NOTION_JOB], "apiVersion": "1"})
    jobs = scraper.fetch_jobs("notion")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "ashby"
    assert job.ats_type == "ashby"
    assert job.source_id == "03143d98-a561-44c6-96a5-fc9bfaf8d18d"
    assert job.company == "Notion"
    assert job.title == "Senior Software Engineer"
    assert job.employment_type == "fulltime"
    assert job.location == "San Francisco, California"
    assert (
        job.application_url
        == "https://jobs.ashbyhq.com/notion/03143d98-a561-44c6-96a5-fc9bfaf8d18d/application"
    )
    assert "Senior Software Engineer" in (job.description or "")
    assert job.raw_data == NOTION_JOB


def test_fetch_jobs_maps_remote_location_and_intern_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper = _scraper_with_response(
        monkeypatch, {"jobs": [REMOTE_INTERN_JOB], "apiVersion": "1"}
    )
    jobs = scraper.fetch_jobs("notion")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.employment_type == "internship"
    assert job.location == "New York, New York, Remote"
    # No applyUrl -> falls back to jobUrl.
    assert job.application_url == "https://jobs.ashbyhq.com/notion/abc-123"


def test_fetch_jobs_does_not_double_append_remote_when_already_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = dict(REMOTE_INTERN_JOB)
    item["location"] = "Remote - US"
    scraper = _scraper_with_response(monkeypatch, {"jobs": [item], "apiVersion": "1"})
    jobs = scraper.fetch_jobs("notion")

    assert jobs[0].location == "Remote - US"


def test_fetch_jobs_falls_back_to_stripped_html_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.intake.html_utils import strip_html

    item = dict(NOTION_JOB)
    item["descriptionPlain"] = ""
    item["descriptionHtml"] = "<p>Full HTML description with <b>details</b>.</p>"
    scraper = _scraper_with_response(monkeypatch, {"jobs": [item], "apiVersion": "1"})
    jobs = scraper.fetch_jobs("notion")

    assert jobs[0].description == strip_html(item["descriptionHtml"])


# ---- malformed responses ---------------------------------------------------


def test_fetch_jobs_skips_malformed_job_item(monkeypatch: pytest.MonkeyPatch) -> None:
    malformed = {"title": "Missing id field"}  # no "id" -> KeyError inside _parse_job
    scraper = _scraper_with_response(monkeypatch, {"jobs": [malformed]})
    jobs = scraper.fetch_jobs("notion")

    assert jobs == []


def test_fetch_jobs_empty_jobs_list_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = _scraper_with_response(monkeypatch, {"jobs": [], "apiVersion": "1"})
    assert scraper.fetch_jobs("vercel") == []


def test_fetch_jobs_raises_scraper_error_on_bad_top_level_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper = _scraper_with_response(monkeypatch, {"jobs": "not-a-list"})
    with pytest.raises(ScraperError):
        scraper.fetch_jobs("notion")


def test_fetch_jobs_raises_scraper_error_on_non_dict_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper = _scraper_with_response(monkeypatch, ["not", "a", "dict"])
    with pytest.raises(ScraperError):
        scraper.fetch_jobs("notion")


# ---- board discovery -------------------------------------------------------


def test_board_discovery_extracts_ashby_slug() -> None:
    from src.intake.board_discovery import discover_board_slugs
    from src.intake.schema import RawJob

    job = RawJob(
        source="ashby",
        source_id="1",
        company="Notion",
        title="Engineer",
        application_url="https://jobs.ashbyhq.com/notion/abc-123/application",
    )
    found = discover_board_slugs([job])
    assert found["ashby"] == {"notion"}
