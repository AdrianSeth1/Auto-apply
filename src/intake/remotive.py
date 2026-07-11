"""Remotive remote-jobs adapter.

Uses Remotive's public Remote Jobs API (free, keyless):
  GET https://remotive.com/api/remote-jobs?search=<keyword>

Response shape (verified live, 2026-07-11): a top-level dict with
``job-count``, ``total-job-count``, and a ``jobs`` list. Each item:
``id``, ``url`` (the Remotive listing page), ``title``, ``company_name``,
``company_logo``, ``category``, ``tags``, ``job_type`` (e.g.
"full_time"), ``publication_date`` (naive ISO datetime, no timezone
suffix), ``candidate_required_location`` (free text -- "Worldwide",
"USA, CST (UTC-6)", etc.), ``salary`` (free text, often present),
``description`` (full HTML, not truncated).

KNOWN CAVEAT (observed live, 2026-07-11): Remotive's API docs
(github.com/remotive-io/remote-jobs-api) document ``search``,
``category``, and ``limit`` query params, but the live endpoint is
served through a Cloudflare cache that appears to key on the path only
-- every combination of ``search``/``category``/``limit`` tested
(including a cache-busting nonce param) returned an IDENTICAL
19-hour-old 30-job snapshot. This may be a temporary Remotive-side
config issue rather than a permanent API change, so the request still
passes ``search`` as documented; correctness does not depend on it
actually filtering server-side, since the caller (src.application.jobs)
already re-applies keyword filtering client-side the same way it does
for every other source that returns unfiltered results.
"""

from __future__ import annotations

import logging

from src.intake.base import BaseScraper, ScraperError
from src.intake.html_utils import strip_html
from src.intake.schema import RawJob, classify_employment_type, classify_seniority

logger = logging.getLogger("autoapply.intake.remotive")

BASE_URL = "https://remotive.com/api/remote-jobs"


class RemotiveScraper(BaseScraper):
    """Scraper for the Remotive remote-jobs API."""

    source_name = "remotive"

    def fetch_jobs(self, keyword: str = "") -> list[RawJob]:  # noqa: D401 -- BaseScraper override
        """Fetch remote jobs, optionally narrowed by a search keyword.

        Args:
            keyword: Free-text search term (maps to Remotive's ``search``
                query param). Empty string fetches the default listing.

        Returns:
            List of normalized RawJob objects.
        """
        params = {"search": keyword} if keyword else {}

        logger.info("Fetching Remotive jobs (keyword=%r)", keyword)

        try:
            data = self._get(BASE_URL, params=params).json()
        except ScraperError:
            raise
        except Exception as e:
            raise ScraperError(f"Failed to parse Remotive response: {e}") from e

        if not isinstance(data, dict):
            raise ScraperError("Unexpected Remotive response shape")
        listings = data.get("jobs")
        if not isinstance(listings, list):
            raise ScraperError("Unexpected Remotive response shape")

        jobs = []
        for item in listings:
            try:
                jobs.append(self._parse_job(item))
            except Exception as e:
                logger.warning("Skipping malformed Remotive job %s: %s", item.get("id"), e)

        logger.info("Fetched %d jobs from Remotive", len(jobs))
        return jobs

    def _parse_job(self, item: dict) -> RawJob:
        """Convert a raw Remotive API job dict to RawJob."""
        job_id = str(item["id"])
        title = (item.get("title") or "").strip()
        company = (item.get("company_name") or "Unknown").strip() or "Unknown"
        location = (item.get("candidate_required_location") or "").strip() or None

        description_html = item.get("description") or ""
        description = strip_html(description_html) if description_html else None

        employment_hint = f"{item.get('job_type') or ''} {title}"
        employment_type = classify_employment_type(employment_hint)
        seniority = classify_seniority(title)

        return RawJob(
            source="remotive",
            source_id=job_id,
            company=company,
            title=title,
            location=location,
            employment_type=employment_type,
            seniority=seniority,
            description=description,
            application_url=item.get("url") or "",
            ats_type="unknown",
            raw_data=item,
        )
