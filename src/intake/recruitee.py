"""Recruitee public careers-site API adapter (disabled until conformance)."""

from __future__ import annotations

from src.intake.base import BaseScraper, ScraperError
from src.intake.html_utils import strip_html
from src.intake.schema import ApplicationTargetV2, JobProvenanceV2, RawJob, classify_employment_type, classify_seniority


class RecruiteeScraper(BaseScraper):
    source_name = "recruitee"

    def fetch_jobs(self, company_slug: str) -> list[RawJob]:
        url = f"https://{company_slug}.recruitee.com/api/offers/"
        jobs: list[RawJob] = []
        provider_records = 0
        seen: set[str] = set()
        while url:
            data = self._get(url).json()
            if not isinstance(data, dict):
                raise ScraperError(f"Unexpected Recruitee response for {company_slug}")
            items = data.get("offers") or data.get("results") or []
            if not isinstance(items, list):
                raise ScraperError(f"Unexpected Recruitee response for {company_slug}")
            provider_records += len(items)
            for item in items:
                try:
                    jobs.append(self._parse_job(company_slug, item))
                except (KeyError, TypeError, ValueError):
                    continue
            next_url = data.get("next") or (data.get("links") or {}).get("next")
            if not isinstance(next_url, str) or not next_url or next_url in seen:
                break
            seen.add(next_url)
            url = next_url
        self.last_fetch_stats = {
            "provider_records": provider_records,
            "normalized_records": len(jobs),
            "malformed_records": provider_records - len(jobs),
        }
        return jobs

    def _parse_job(self, company_slug: str, item: dict) -> RawJob:
        job_id = str(item.get("id") or item["slug"])
        title = str(item.get("title") or "").strip()
        location_data = item.get("location") or item.get("locations") or ""
        if isinstance(location_data, dict):
            location = ", ".join(str(location_data.get(key)).strip() for key in ("city", "state", "country") if location_data.get(key))
        elif isinstance(location_data, list):
            location = "; ".join(str(value.get("name") if isinstance(value, dict) else value) for value in location_data)
        else:
            location = str(location_data).strip()
        description_html = item.get("description") or item.get("description_html") or ""
        description = strip_html(str(description_html)) if description_html else None
        slug = str(item.get("slug") or job_id)
        listing_url = str(item.get("careers_url") or item.get("url") or f"https://{company_slug}.recruitee.com/o/{slug}")
        apply_url = str(item.get("apply_url") or f"{listing_url}/c/new")
        return RawJob(
            source="recruitee", source_id=job_id, company=str(item.get("company_name") or company_slug.replace("-", " ").title()),
            title=title, location=location or None, employment_type=classify_employment_type(str(item.get("employment_type") or title)),
            seniority=classify_seniority(title), description=description, application_url=apply_url,
            ats_type="recruitee", raw_data=item,
            provenance=JobProvenanceV2(adapter="recruitee", channel="direct_ats", listing_url=listing_url,
                publisher_relationship="employer_verified", description_completeness="full" if description and len(description) >= 300 else "partial",
                application_target=ApplicationTargetV2(original_url=apply_url, resolved_url=apply_url, kind="direct_ats", resolution_status="provider_supplied"), parser_confidence=0.85),
        )


__all__ = ["RecruiteeScraper"]

