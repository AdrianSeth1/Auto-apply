"""SmartRecruiters public company-postings adapter (disabled until conformance)."""

from __future__ import annotations

from src.intake.base import BaseScraper, ScraperError
from src.intake.html_utils import strip_html
from src.intake.schema import JobProvenanceV2, ApplicationTargetV2, RawJob, classify_employment_type, classify_seniority

BASE_URL = "https://api.smartrecruiters.com/v1/companies"


class SmartRecruitersScraper(BaseScraper):
    source_name = "smartrecruiters"

    def fetch_jobs(self, company_slug: str) -> list[RawJob]:
        jobs: list[RawJob] = []
        provider_records = 0
        offset, limit = 0, 100
        while True:
            data = self._get(f"{BASE_URL}/{company_slug}/postings", params={"limit": limit, "offset": offset}).json()
            if not isinstance(data, dict) or not isinstance(data.get("content", []), list):
                raise ScraperError(f"Unexpected SmartRecruiters response for {company_slug}")
            content = data.get("content", [])
            provider_records += len(content)
            for summary in content:
                try:
                    detail = self._get(f"{BASE_URL}/{company_slug}/postings/{summary['id']}").json()
                    jobs.append(self._parse_job(company_slug, detail))
                except (KeyError, TypeError, ValueError, ScraperError):
                    continue
            offset += len(content)
            total = int(data.get("totalFound") or offset)
            if not content or offset >= total:
                break
        self.last_fetch_stats = {
            "provider_records": provider_records,
            "normalized_records": len(jobs),
            "malformed_records": provider_records - len(jobs),
        }
        return jobs

    def _parse_job(self, company_slug: str, item: dict) -> RawJob:
        job_id = str(item["id"])
        title = str(item.get("name") or "").strip()
        company = str((item.get("company") or {}).get("name") or company_slug.replace("-", " ").title())
        location_data = item.get("location") or {}
        location = ", ".join(str(location_data.get(key)).strip() for key in ("city", "region", "country") if location_data.get(key)) or None
        sections = (item.get("jobAd") or {}).get("sections") or {}
        description = "\n\n".join(strip_html(str((sections.get(key) or {}).get("text") or "")) for key in ("jobDescription", "qualifications", "additionalInformation") if (sections.get(key) or {}).get("text")) or None
        apply_url = str(item.get("applyUrl") or item.get("ref") or f"https://jobs.smartrecruiters.com/{company_slug}/{job_id}")
        return RawJob(
            source="smartrecruiters", source_id=job_id, company=company, title=title,
            location=location, employment_type=classify_employment_type(str(item.get("typeOfEmployment") or title)),
            seniority=classify_seniority(title), description=description, application_url=apply_url,
            ats_type="smartrecruiters", raw_data=item,
            provenance=JobProvenanceV2(
                adapter="smartrecruiters", channel="direct_ats", source_record_url=str(item.get("ref") or "") or None,
                listing_url=str(item.get("ref") or apply_url), publisher_relationship="employer_verified",
                description_completeness="full" if description and len(description) >= 300 else "partial",
                application_target=ApplicationTargetV2(original_url=apply_url, resolved_url=apply_url, kind="direct_ats", resolution_status="provider_supplied"),
                parser_confidence=0.9,
            ),
        )


__all__ = ["SmartRecruitersScraper"]

