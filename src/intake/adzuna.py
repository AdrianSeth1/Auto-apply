"""Adzuna job-search API adapter.

Uses Adzuna's public Job Search API (free tier, requires app_id + app_key):
  GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}

Register a free app_id/app_key at https://developer.adzuna.com. The key is
a secret -- set it via the env var named by ``adzuna.app_key_env`` in
config/settings.yaml (default ``AUTOAPPLY_ADZUNA_KEY``), never commit it.

Response shape (verified against the public Adzuna docs, 2026-07): a
top-level dict with a ``results`` list. Each job dict: ``id``, ``title``,
``company`` (``{"display_name": ...}``), ``location``
(``{"display_name": ..., "area": [...]}``), ``description`` (a TRUNCATED
snippet -- the JD-recovery fetcher pulls the full text at generation time
via ``redirect_url``), ``redirect_url``, ``created`` (ISO datetime
string), ``salary_min``, ``salary_max``, ``salary_is_predicted``,
``contract_type`` ("permanent"/"contract"), ``contract_time``
("full_time"/"part_time"), ``category`` (``{"label", "tag"}``).

Unlike the board scrapers (Greenhouse/Lever/Ashby), Adzuna is a keyword
search API, not a per-company board -- there's no company slug, so this
does not implement ``BaseScraper.fetch_jobs(company_slug)``. It reuses
``BaseScraper``'s default timeout/headers and ``ScraperError``.
"""

from __future__ import annotations

import logging

import httpx

from src.intake.base import DEFAULT_HEADERS, DEFAULT_TIMEOUT, ScraperError
from src.intake.schema import RawJob, classify_employment_type, classify_seniority

logger = logging.getLogger("autoapply.intake.adzuna")

BASE_URL = "https://api.adzuna.com/v1/api/jobs"


class AdzunaScraper:
    """Scraper for the Adzuna job search API."""

    source_name = "adzuna"

    def __init__(
        self,
        app_id: str,
        app_key: str,
        country: str = "us",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        if not app_id or not app_key:
            raise ScraperError("Adzuna app_id and app_key are required")
        self.app_id = app_id
        self.app_key = app_key
        self.country = country
        self._client = httpx.Client(
            timeout=timeout, headers=DEFAULT_HEADERS, follow_redirects=True
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AdzunaScraper:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def search(
        self,
        keyword: str,
        location: str = "",
        page: int = 1,
        results_per_page: int = 50,
    ) -> list[RawJob]:
        """Fetch one page of Adzuna search results for a keyword.

        Args:
            keyword: Free-text search term (maps to ``what``).
            location: Free-text location filter (maps to ``where``).
            page: 1-indexed result page.
            results_per_page: Page size (Adzuna caps this per-plan).

        Returns:
            List of normalized RawJob objects.
        """
        url = f"{BASE_URL}/{self.country}/search/{page}"
        params: dict[str, str | int] = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "results_per_page": results_per_page,
            "content-type": "application/json",
        }
        if keyword:
            params["what"] = keyword
        if location:
            params["where"] = location

        logger.info(
            "Fetching Adzuna jobs for keyword=%r location=%r page=%d",
            keyword,
            location,
            page,
        )

        try:
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ScraperError(f"HTTP {e.response.status_code} from Adzuna") from e
        except httpx.RequestError as e:
            raise ScraperError(f"Adzuna request failed: {e}") from e

        try:
            data = resp.json()
        except Exception as e:
            raise ScraperError(f"Failed to parse Adzuna response: {e}") from e

        if not isinstance(data, dict):
            raise ScraperError("Unexpected Adzuna response shape")
        results = data.get("results", [])
        if not isinstance(results, list):
            raise ScraperError("Unexpected Adzuna response shape")

        jobs = []
        for item in results:
            try:
                jobs.append(self._parse_job(item))
            except Exception as e:
                logger.warning("Skipping malformed Adzuna job %s: %s", item.get("id"), e)

        logger.info("Fetched %d jobs from Adzuna (keyword=%r)", len(jobs), keyword)
        return jobs

    def _parse_job(self, item: dict) -> RawJob:
        """Convert a raw Adzuna API job dict to RawJob."""
        job_id = str(item["id"])
        title = (item.get("title") or "").strip()

        company_obj = item.get("company") or {}
        company = (company_obj.get("display_name") or "Unknown").strip() or "Unknown"

        location_obj = item.get("location") or {}
        location = (location_obj.get("display_name") or "").strip() or None

        # Combine (not OR) with the title: Adzuna's contract_time/contract_type
        # are often stale/generic ("full_time") even on postings whose title
        # makes the real type obvious (e.g. an internship). Combining lets
        # classify_employment_type's ordered keyword scan -- which checks
        # "intern" before "full" -- prefer the more specific title signal.
        employment_hint = " ".join(
            filter(None, (item.get("contract_time"), item.get("contract_type"), title))
        ).strip()
        employment_type = classify_employment_type(employment_hint)
        seniority = classify_seniority(title)

        description = (item.get("description") or "").strip() or None
        application_url = item.get("redirect_url") or ""

        return RawJob(
            source="adzuna",
            source_id=job_id,
            company=company,
            title=title,
            location=location,
            employment_type=employment_type,
            seniority=seniority,
            description=description,
            application_url=application_url,
            ats_type="unknown",
            raw_data=item,
        )
