"""Workday CXS (Candidate Experience Site) intake adapter.

Each Workday tenant exposes a public, keyless JSON careers API:
  POST https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
  body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}

Response shape and quirks below were verified live (2026-07-11) against
five real tenants: salesforce (wd12/External_Career_Site), adobe
(wd5/external_experienced), workday itself (wd5/Workday), athenahealth
(wd1/External), healthcatalyst (wd5/healthcatalystcareers).

List response: ``{"total": N, "jobPostings": [...], "facets": [...]}``.
Each item: ``title``, ``externalPath`` (starts with "/job/..."),
``locationsText`` (a single location string, OR the literal "N
Locations" when there are several -- the list endpoint doesn't expand
them), ``postedOn`` (a RELATIVE human string -- "Posted Today" /
"Posted Yesterday" / "Posted N Days Ago" / "Posted 30+ Days Ago" -- NOT
an ISO date), ``bulletFields`` (a list of short strings whose CONTENTS
VARY BY TENANT: some carry only the req id, others prepend the
employment type and/or legal-entity name -- nothing here is safe to
index positionally), and sometimes a top-level ``timeType`` (e.g.
"Full time"). NOTE: unlike Greenhouse/Lever/Ashby, list items carry NO
description field at all (checked live across every tenant above, not
just one) -- not even a snippet. That makes the detail-fetch step below
load-bearing rather than a nice-to-have: a Workday job that never gets
detail-fetched has an empty description, and JD-recovery at generation
time is unlikely to save it either (Workday career pages are
client-rendered SPAs; a plain httpx GET of ``application_url`` mostly
returns an empty shell).

``limit`` has a hard server-side cap of 20 -- confirmed live (21+
returns ``HTTP_400``). ``total`` is only reliable on the first page; on
later pages Workday has been observed returning ``total: 0`` even when
more jobs remain, so pagination here stops on a short page instead of
trusting ``total``.

Job detail (``GET .../wday/cxs/{tenant}/{site}{externalPath}`` -- note
``externalPath`` already starts with "/job/...") returns
``{"jobPostingInfo": {"jobDescription" (HTML), "location",
"additionalLocations", "postedOn" (same relative text), "startDate"
(an actual ISO date -- observed to match the true posting date, more
precise than the relative "postedOn" text), "timeType", "jobReqId",
"remoteType", "externalUrl", ...}, "hiringOrganization": {...}}``.

A wrong/dead tenant or site doesn't necessarily fail the connection --
it 400/404/422s with a Workday JSON error body just like a valid tenant
with a bad request would. Both cases (and genuine connection failures)
are surfaced as ``ScraperError`` so callers can skip-and-continue like
any other board (self-pruning, matching greenhouse.py/lever.py).

Tenant/site naming is NOT standardized (PascalCase vs lowercase, and
the "site" segment is an arbitrary per-tenant label chosen when the
career site was set up) -- there is no way to derive host/site from the
tenant name alone, so companies.yaml's ``workday:`` entries carry the
exact ``{tenant, host, site}`` triple copied from each company's live
myworkdayjobs.com URL.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

import httpx

from src.intake.base import BaseScraper, ScraperError
from src.intake.html_utils import strip_html
from src.intake.schema import RawJob, classify_employment_type, classify_seniority

logger = logging.getLogger("autoapply.intake.workday")

PAGE_SIZE = 20  # server-enforced hard cap -- verified live, 21+ -> HTTP_400
MAX_JOBS_PER_BOARD = 200
DETAIL_FETCH_CAP = 30

_RELATIVE_POSTED_RE = re.compile(r"posted\s+(\d+)\+?\s+days?\s+ago", re.IGNORECASE)


class WorkdayScraper(BaseScraper):
    """Scraper for a single Workday CXS tenant/site.

    Doesn't implement ``BaseScraper.fetch_jobs(company_slug: str)`` in
    the literal sense -- a bare slug string can't determine host/site,
    so ``fetch_jobs`` here takes the ``{tenant, host, site}`` dict from
    companies.yaml instead. Still inherits ``BaseScraper`` for the
    shared httpx client lifecycle / context-manager convention.
    """

    source_name = "workday"

    def fetch_jobs(self, tenant_config: dict) -> list[RawJob]:
        """Fetch jobs for one Workday tenant, paginated to MAX_JOBS_PER_BOARD.

        Args:
            tenant_config: ``{"tenant": ..., "host": ..., "site": ...}``.

        Returns:
            List of normalized RawJob objects using the list-endpoint's
            short description. Call ``fetch_job_detail`` for the full
            JD on jobs that survive keyword filtering.
        """
        tenant = tenant_config["tenant"]
        host = tenant_config["host"]
        site = tenant_config["site"]
        base_url = f"https://{tenant}.{host}.myworkdayjobs.com"
        list_url = f"{base_url}/wday/cxs/{tenant}/{site}/jobs"

        logger.info("Fetching Workday jobs for '%s/%s'", tenant, site)

        jobs: list[RawJob] = []
        offset = 0
        while offset < MAX_JOBS_PER_BOARD:
            limit = min(PAGE_SIZE, MAX_JOBS_PER_BOARD - offset)
            body = {
                "appliedFacets": {},
                "limit": limit,
                "offset": offset,
                "searchText": "",
            }
            data = self._post_json(list_url, body, tenant=tenant, site=site)

            postings = data.get("jobPostings")
            if postings is None or not isinstance(postings, list):
                raise ScraperError(f"Unexpected Workday response shape for {tenant}/{site}")
            if not postings:
                break

            for item in postings:
                try:
                    jobs.append(self._parse_job(tenant, host, site, base_url, item))
                except Exception as e:
                    logger.warning(
                        "Skipping malformed Workday job at %s/%s: %s", tenant, site, e
                    )

            offset += len(postings)
            if len(postings) < limit:
                break  # short page -- last page, regardless of "total"

        logger.info("Fetched %d jobs from Workday/%s/%s", len(jobs), tenant, site)
        return jobs

    def fetch_job_detail(self, job: RawJob) -> RawJob:
        """Fetch the full JD for one job and return an updated copy.

        Best-effort: on any failure the original ``job`` is returned
        unchanged (callers -- see ``DETAIL_FETCH_CAP`` gating in
        ``src.application.jobs`` -- treat this as an enhancement, not a
        hard dependency, mirroring the LinkedIn detail-enrichment
        convention).
        """
        detail_url = (job.raw_data or {}).get("workday_detail_url")
        if not detail_url:
            return job
        try:
            resp = self._client.get(detail_url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.info("Workday detail fetch failed for %s: %s", detail_url, e)
            return job

        info = data.get("jobPostingInfo") if isinstance(data, dict) else None
        if not isinstance(info, dict):
            return job

        description_html = info.get("jobDescription") or ""
        description = strip_html(description_html) if description_html else job.description

        raw_data = dict(job.raw_data or {})
        raw_data["workday_detail"] = info
        start_date = info.get("startDate")
        if start_date:
            # More precise than the relative "postedOn" text -- prefer it.
            raw_data["workday_posted_date"] = start_date

        return job.model_copy(update={"description": description, "raw_data": raw_data})

    def _post_json(self, url: str, json_body: dict, *, tenant: str, site: str) -> dict:
        try:
            resp = self._client.post(url, json=json_body)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ScraperError(
                f"HTTP {e.response.status_code} from Workday {tenant}/{site}"
            ) from e
        except httpx.RequestError as e:
            raise ScraperError(f"Workday request failed for {tenant}/{site}: {e}") from e

        try:
            data = resp.json()
        except Exception as e:
            raise ScraperError(f"Failed to parse Workday response for {tenant}/{site}: {e}") from e

        if not isinstance(data, dict):
            raise ScraperError(f"Unexpected Workday response shape for {tenant}/{site}")
        # A bad tenant/site doesn't always fail the HTTP request -- it can
        # 200 with a Workday-native error body instead.
        if "errorCode" in data or "jobPostings" not in data:
            reason = data.get("errorCode") or "malformed response"
            raise ScraperError(f"Workday error for {tenant}/{site}: {reason}")
        return data

    def _parse_job(self, tenant: str, host: str, site: str, base_url: str, item: dict) -> RawJob:
        """Convert a raw Workday list-item dict to RawJob."""
        external_path = item["externalPath"]
        title = (item.get("title") or "").strip()
        location = (item.get("locationsText") or "").strip() or None

        # Combine (not OR) with the title: bulletFields/timeType content
        # varies by tenant and is sometimes generic ("Regular"), so let
        # classify_employment_type's ordered keyword scan -- which checks
        # "intern" before "full" -- prefer a more specific title signal.
        employment_hint = " ".join(
            filter(
                None,
                [item.get("timeType"), *(item.get("bulletFields") or []), title],
            )
        )
        employment_type = classify_employment_type(employment_hint)
        seniority = classify_seniority(title)

        raw_data = dict(item)
        raw_data["workday_tenant"] = tenant
        # Human-facing application URL (browsable career-site page).
        application_url = f"{base_url}/{site}{external_path}"
        # Internal CXS API URL used for the (capped, keyword-gated) detail
        # fetch -- deliberately kept separate from application_url so it
        # never leaks out as something a human/browser would visit.
        raw_data["workday_detail_url"] = f"{base_url}/wday/cxs/{tenant}/{site}{external_path}"
        posted_date = _parse_relative_posted(item.get("postedOn"))
        if posted_date:
            raw_data["workday_posted_date"] = posted_date

        return RawJob(
            source="workday",
            source_id=external_path,
            company=_infer_company_name(tenant),
            title=title,
            location=location,
            employment_type=employment_type,
            seniority=seniority,
            description=None,
            application_url=application_url,
            ats_type="workday",
            raw_data=raw_data,
        )


def fetch_company_jobs(tenant_config: dict) -> list[RawJob]:
    """Convenience entry point: fetch + auto-close a WorkdayScraper session."""
    with WorkdayScraper() as scraper:
        return scraper.fetch_jobs(tenant_config)


def _infer_company_name(tenant: str) -> str:
    """Get company name from the tenant slug (Workday's CXS API doesn't
    return a company display name -- same fallback convention as
    ashby.py/lever.py)."""
    return tenant.replace("-", " ").replace("_", " ").title()


def _parse_relative_posted(text: str | None) -> str | None:
    """Best-effort conversion of Workday's relative ``postedOn`` text
    into an approximate ISO date. Returns ``None`` for unrecognized text
    rather than guessing (same "unknown passes" convention used
    elsewhere -- see scorer._posting_age_days).
    """
    if not text:
        return None
    s = text.strip().lower()
    if s == "posted today":
        days = 0
    elif s == "posted yesterday":
        days = 1
    else:
        match = _RELATIVE_POSTED_RE.match(s)
        if not match:
            return None
        days = int(match.group(1))
    return (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
