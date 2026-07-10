"""Ashby ATS scraper.

Uses Ashby's public Job Board Posting API (no auth required):
  GET https://api.ashbyhq.com/posting-api/job-board/{slug}

Company slugs match the Ashby job board URL:
  https://jobs.ashbyhq.com/notion  → slug = "notion"

Response shape: ``{"jobs": [...], "apiVersion": "1"}``. Each job dict
(verified live against the "notion" board 2026-07): ``id``, ``title``,
``department``, ``team``, ``employmentType``, ``location`` (plain
string), ``secondaryLocations`` (list of ``{"location": str, ...}``),
``isRemote`` (bool | None), ``workplaceType`` (str | None),
``publishedAt`` (ISO datetime string), ``jobUrl``, ``applyUrl``,
``descriptionHtml``, ``descriptionPlain``.
"""

from __future__ import annotations

import logging

from src.intake.base import BaseScraper, ScraperError
from src.intake.html_utils import strip_html
from src.intake.schema import RawJob, classify_employment_type, classify_seniority

logger = logging.getLogger("autoapply.intake.ashby")

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"


class AshbyScraper(BaseScraper):
    """Scraper for Ashby job boards."""

    source_name = "ashby"

    def fetch_jobs(self, company_slug: str) -> list[RawJob]:
        """Fetch all open jobs for a company's Ashby board.

        Args:
            company_slug: The Ashby board slug (e.g. "notion", "linear").

        Returns:
            List of normalized RawJob objects.
        """
        url = f"{BASE_URL}/{company_slug}"

        logger.info("Fetching Ashby jobs for '%s'", company_slug)

        try:
            data = self._get(url).json()
        except ScraperError:
            raise
        except Exception as e:
            raise ScraperError(f"Failed to parse Ashby response for {company_slug}: {e}") from e

        if not isinstance(data, dict):
            raise ScraperError(f"Unexpected Ashby response shape for {company_slug}")
        raw_jobs_list = data.get("jobs", [])
        if not isinstance(raw_jobs_list, list):
            raise ScraperError(f"Unexpected Ashby response shape for {company_slug}")

        jobs = []
        for item in raw_jobs_list:
            try:
                job = self._parse_job(company_slug, item)
                jobs.append(job)
            except Exception as e:
                logger.warning("Skipping malformed Ashby job %s: %s", item.get("id"), e)

        logger.info("Fetched %d jobs from Ashby/%s", len(jobs), company_slug)
        return jobs

    def _parse_job(self, company_slug: str, item: dict) -> RawJob:
        """Convert a raw Ashby API job dict to RawJob."""
        job_id = str(item["id"])
        title = (item.get("title") or "").strip()

        location = _format_location(item)

        employment_type = classify_employment_type(item.get("employmentType", "") or title)
        seniority = classify_seniority(title)

        description_plain = (item.get("descriptionPlain") or "").strip()
        if description_plain:
            description = description_plain
        else:
            description_html = item.get("descriptionHtml", "")
            description = strip_html(description_html) if description_html else None

        application_url = item.get("applyUrl") or item.get("jobUrl") or ""
        if not application_url:
            application_url = f"https://jobs.ashbyhq.com/{company_slug}/{job_id}"

        return RawJob(
            source="ashby",
            source_id=job_id,
            company=_infer_company_name(company_slug, item),
            title=title,
            location=location or None,
            employment_type=employment_type,
            seniority=seniority,
            description=description,
            application_url=application_url,
            ats_type="ashby",
            raw_data=item,
        )


def _format_location(item: dict) -> str:
    """Primary location string, with a ", Remote" suffix when
    ``isRemote`` is true and the location text doesn't already say so
    (mirrors how ``greenhouse.py`` prefers the primary office over the
    full location list)."""
    location = (item.get("location") or "").strip()
    if item.get("isRemote") and "remote" not in location.lower():
        location = f"{location}, Remote" if location else "Remote"
    return location


def _infer_company_name(slug: str, item: dict) -> str:
    """Get company name, falling back to slug formatting.

    Ashby's job-board API doesn't return a company object, so this
    always falls back to the slug (matches ``lever.py``'s convention).
    """
    return slug.replace("-", " ").replace("_", " ").title()
