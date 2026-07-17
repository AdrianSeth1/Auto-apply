"""Workable public careers adapter (disabled until live conformance)."""

from __future__ import annotations

from urllib.parse import urljoin

from src.intake.base import BaseScraper, ScraperError
from src.intake.html_utils import strip_html
from src.intake.schema import ApplicationTargetV2, JobProvenanceV2, RawJob, classify_employment_type, classify_seniority

BASE_URL = "https://apply.workable.com/api/v3/accounts"


class WorkablePublicScraper(BaseScraper):
    source_name = "workable"

    def fetch_jobs(self, company_slug: str) -> list[RawJob]:
        url = f"{BASE_URL}/{company_slug}/jobs"
        params: dict[str, object] = {"limit": 100}
        jobs: list[RawJob] = []
        provider_records = 0
        seen_pages: set[str] = set()
        while url:
            data = self._get(url, params=params).json()
            params = {}
            if not isinstance(data, dict):
                raise ScraperError(f"Unexpected Workable response for {company_slug}")
            items = data.get("results") or data.get("jobs") or []
            if not isinstance(items, list):
                raise ScraperError(f"Unexpected Workable response for {company_slug}")
            provider_records += len(items)
            for summary in items:
                try:
                    shortcode = str(summary.get("shortcode") or summary["id"])
                    detail = self._get(f"{BASE_URL}/{company_slug}/jobs/{shortcode}").json()
                    jobs.append(self._parse_job(company_slug, detail))
                except (KeyError, TypeError, ValueError, ScraperError):
                    continue
            next_url = data.get("next") or data.get("next_page")
            if isinstance(next_url, int):
                url, params = f"{BASE_URL}/{company_slug}/jobs", {"limit": 100, "page": next_url}
            elif isinstance(next_url, str) and next_url and next_url not in seen_pages:
                seen_pages.add(next_url)
                url = urljoin(BASE_URL, next_url)
            else:
                url = ""
        self.last_fetch_stats = {
            "provider_records": provider_records,
            "normalized_records": len(jobs),
            "malformed_records": provider_records - len(jobs),
        }
        return jobs

    def _parse_job(self, company_slug: str, item: dict) -> RawJob:
        job_id = str(item.get("shortcode") or item["id"])
        title = str(item.get("title") or item.get("name") or "").strip()
        location_data = item.get("location") or {}
        location = location_data if isinstance(location_data, str) else ", ".join(str(location_data.get(key)).strip() for key in ("city", "region", "country") if location_data.get(key))
        description_html = item.get("description") or item.get("description_html") or ""
        description = strip_html(str(description_html)) if description_html else None
        apply_url = str(item.get("application_url") or item.get("url") or f"https://apply.workable.com/{company_slug}/j/{job_id}/")
        return RawJob(
            source="workable", source_id=job_id, company=str(item.get("company_name") or company_slug.replace("-", " ").title()),
            title=title, location=location or None, employment_type=classify_employment_type(str(item.get("employment_type") or title)),
            seniority=classify_seniority(title), description=description, application_url=apply_url,
            ats_type="workable", raw_data=item,
            provenance=JobProvenanceV2(adapter="workable", channel="direct_ats", listing_url=apply_url,
                publisher_relationship="employer_verified", description_completeness="full" if description and len(description) >= 300 else "partial",
                application_target=ApplicationTargetV2(original_url=apply_url, resolved_url=apply_url, kind="direct_ats", resolution_status="provider_supplied"), parser_confidence=0.85),
        )


__all__ = ["WorkablePublicScraper"]

