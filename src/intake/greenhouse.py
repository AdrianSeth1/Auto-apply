"""Greenhouse ATS scraper.

Uses Greenhouse's public Job Board API (no auth required):
  GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs
  GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}

Company tokens can be found in the URL of any Greenhouse job board:
  https://boards.greenhouse.io/stripe  → token = "stripe"
"""

from __future__ import annotations

import logging

from src.intake.base import BaseScraper, ScraperError
from src.intake.html_utils import strip_html
from src.intake.schema import RawJob, classify_employment_type, classify_seniority

logger = logging.getLogger("autoapply.intake.greenhouse")

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseScraper(BaseScraper):
    """Scraper for Greenhouse job boards."""

    source_name = "greenhouse"

    def fetch_jobs(self, company_slug: str) -> list[RawJob]:
        """Fetch all open jobs for a company's Greenhouse board.

        Args:
            company_slug: The Greenhouse board token (e.g. "stripe", "airbnb").

        Returns:
            List of normalized RawJob objects.
        """
        url = f"{BASE_URL}/{company_slug}/jobs"
        params = {"content": "true"}  # include full description

        logger.info("Fetching Greenhouse jobs for '%s'", company_slug)

        try:
            data = self._get(url, params=params).json()
        except ScraperError:
            raise
        except Exception as e:
            raise ScraperError(
                f"Failed to parse Greenhouse response for {company_slug}: {e}"
            ) from e

        raw_jobs_list = data.get("jobs", [])
        if not isinstance(raw_jobs_list, list):
            raise ScraperError(f"Unexpected Greenhouse response shape for {company_slug}")

        jobs = []
        for item in raw_jobs_list:
            try:
                job = self._parse_job(company_slug, item)
                jobs.append(job)
            except Exception as e:
                logger.warning("Skipping malformed Greenhouse job %s: %s", item.get("id"), e)

        self.last_fetch_stats = {
            "provider_records": len(raw_jobs_list),
            "normalized_records": len(jobs),
            "malformed_records": len(raw_jobs_list) - len(jobs),
        }
        logger.info("Fetched %d jobs from Greenhouse/%s", len(jobs), company_slug)
        return jobs

    def fetch_job(self, company_slug: str, job_id: str) -> RawJob:
        """Fetch a single Greenhouse job posting."""
        url = f"{BASE_URL}/{company_slug}/jobs/{job_id}"
        params = {"content": "true"}

        logger.info("Fetching Greenhouse job %s/%s", company_slug, job_id)

        try:
            item = self._get(url, params=params).json()
        except ScraperError:
            raise
        except Exception as e:
            raise ScraperError(
                f"Failed to parse Greenhouse job {company_slug}/{job_id}: {e}"
            ) from e

        if not isinstance(item, dict) or not item.get("id"):
            raise ScraperError(f"Unexpected Greenhouse job response for {company_slug}/{job_id}")

        return self._parse_job(company_slug, item)

    def _parse_job(self, company_slug: str, item: dict) -> RawJob:
        """Convert a raw Greenhouse API job dict to RawJob."""
        job_id = str(item["id"])
        title = item.get("title", "").strip()

        # Location: Greenhouse returns a list of offices
        offices = item.get("offices", [])
        location = (
            offices[0].get("name", "")
            if offices and isinstance(offices[0], dict)
            else item.get("location", {}).get("name", "")
        )

        # Employment type from departments or metadata (Greenhouse doesn't always expose this)
        # Fall back to title-based inference
        employment_type = classify_employment_type(item.get("employment_type", "") or title)
        seniority = classify_seniority(title)

        # Compensation: boards sometimes publish salary via custom metadata
        # fields (e.g. "Budgeted Salary" with value_type "currency"). Promote
        # them to the normalized raw_data keys job_facts already reads
        # (salary_min / salary_max) so pay-aware scoring sees them.
        pay_min, pay_max = _extract_pay_metadata(item)
        if pay_min is not None or pay_max is not None:
            item.setdefault("salary_min", pay_min if pay_min is not None else pay_max)
            item.setdefault("salary_max", pay_max if pay_max is not None else pay_min)

        # Description
        description_html = item.get("content", "")
        description = strip_html(description_html) if description_html else None

        # Application URL
        absolute_url = item.get("absolute_url", "")
        if not absolute_url:
            absolute_url = f"https://boards.greenhouse.io/{company_slug}/jobs/{job_id}"

        return RawJob(
            source="greenhouse",
            source_id=job_id,
            company=_infer_company_name(company_slug, item),
            title=title,
            location=location or None,
            employment_type=employment_type,
            seniority=seniority,
            description=description,
            application_url=absolute_url,
            ats_type="greenhouse",
            raw_data=item,
        )


def _infer_company_name(slug: str, item: dict) -> str:
    """Try to get a proper company name from the job data.

    The board-level API returns the employer display name as a flat
    ``company_name`` string on every job (e.g. ``"First Due"`` for the
    ``localitymediallcdbafirstdue`` board). The nested ``company.name``
    shape exists on some older/other responses. Only fall back to
    title-casing the board slug when neither is present — slug casing
    produced letters addressed to "Localitymediallcdbafirstdue".
    """
    flat_name = _sanitize_company_name(item.get("company_name"))
    if flat_name:
        return flat_name
    nested = item.get("company")
    if isinstance(nested, dict):
        name = _sanitize_company_name(nested.get("name"))
        if name:
            return name
    # Fall back to slug with basic formatting
    return slug.replace("-", " ").replace("_", " ").title()


def _sanitize_company_name(value: object) -> str | None:
    """Clean an employer-published display name for storage/letters.

    Observed in the wild: zero-width/direction marks (ConnectWise ships
    ``‎`` in front of its name) which would render invisibly in a
    letter but corrupt matching and copy/paste.
    """
    if not isinstance(value, str):
        return None
    cleaned = "".join(
        ch for ch in value if ch.isprintable() and ch not in "‎‏​﻿"
    )
    cleaned = " ".join(cleaned.split())
    return cleaned or None


def _extract_pay_metadata(item: dict) -> tuple[int | None, int | None]:
    """Pull salary figures out of Greenhouse custom metadata fields.

    Boards publish compensation in ``metadata`` entries such as::

        {"name": "Budgeted Salary", "value_type": "currency",
         "value": {"amount": "90000.0", "unit": "USD"}}
        {"name": "Salary Range", "value_type": "currency_range",
         "value": {"min_value": "80000", "max_value": "110000"}}

    Only names that look compensation-related are considered, and only
    plausible annual amounts (>= 10,000) are accepted so hourly rates or
    unrelated currency fields don't masquerade as salaries.
    """

    def _amount(value: object) -> int | None:
        try:
            number = float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None
        if number < 10_000:  # hourly rates / bonuses / junk
            return None
        return int(number)

    pay_min: int | None = None
    pay_max: int | None = None
    for entry in item.get("metadata") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").casefold()
        if not any(term in name for term in ("salary", "compensation", "pay")):
            continue
        value = entry.get("value")
        value_type = str(entry.get("value_type") or "")
        if value_type == "currency" and isinstance(value, dict):
            amount = _amount(value.get("amount"))
            if amount is not None:
                pay_min = amount if pay_min is None else min(pay_min, amount)
                pay_max = amount if pay_max is None else max(pay_max, amount)
        elif value_type == "currency_range" and isinstance(value, dict):
            low = _amount(value.get("min_value"))
            high = _amount(value.get("max_value"))
            if low is not None:
                pay_min = low if pay_min is None else min(pay_min, low)
            if high is not None:
                pay_max = high if pay_max is None else max(pay_max, high)
    return pay_min, pay_max
