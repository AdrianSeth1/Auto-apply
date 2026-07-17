"""Official Hacker News ``jobstories`` startup-job adapter.

Uses the documented public Firebase API, not browser scraping. The feed exposes
up to 200 current job items and has no published rate limit. Results are cached
for 15 minutes to keep repeated profile searches cheap.
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx

from src.intake.base import DEFAULT_HEADERS, DEFAULT_TIMEOUT, ScraperError
from src.intake.html_utils import strip_html
from src.intake.schema import RawJob, classify_employment_type, classify_seniority

API_ROOT = "https://hacker-news.firebaseio.com/v0"
_CACHE_TTL_SECONDS = 900
_CACHE_LOCK = threading.Lock()
_CACHE: tuple[float, list[RawJob]] | None = None

_HIRING_RE = re.compile(
    r"^(?P<company>[^|.!?\r\n]{1,100}?)\s+(?:is\s+)?hiring\s+(?P<title>.+)$",
    re.I,
)
_AT_RE = re.compile(r"^(?P<title>.+?)\s+at\s+(?P<company>[^|–—-]+)$", re.I)


def fetch_hn_jobstories(
    *, force_refresh: bool = False, timeout: int = DEFAULT_TIMEOUT, limit: int = 200
) -> list[RawJob]:
    global _CACHE

    with _CACHE_LOCK:
        if not force_refresh and _CACHE and time.monotonic() - _CACHE[0] < _CACHE_TTL_SECONDS:
            return [job.model_copy(deep=True) for job in _CACHE[1]]

    try:
        with httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS) as client:
            response = client.get(f"{API_ROOT}/jobstories.json")
            response.raise_for_status()
            ids = response.json()
    except Exception as exc:
        raise ScraperError(f"HN jobstories fetch failed: {exc}") from exc
    if not isinstance(ids, list):
        raise ScraperError("Unexpected HN jobstories response shape")

    selected_ids = [int(item_id) for item_id in ids[: max(1, min(limit, 200))]]

    def fetch_one(item_id: int) -> RawJob | None:
        try:
            with httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS) as client:
                response = client.get(f"{API_ROOT}/item/{item_id}.json")
                response.raise_for_status()
                item = response.json()
            return _parse_job_item(item)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        jobs = [job for job in pool.map(fetch_one, selected_ids) if job is not None]

    with _CACHE_LOCK:
        _CACHE = (time.monotonic(), [job.model_copy(deep=True) for job in jobs])
    return jobs


def _parse_job_item(item: dict) -> RawJob | None:
    if not isinstance(item, dict) or item.get("type") != "job" or item.get("dead"):
        return None
    item_id = item.get("id")
    raw_title = strip_html(str(item.get("title") or "")).strip()
    description = strip_html(str(item.get("text") or "")).strip()
    if not item_id or not raw_title:
        return None

    company, title = _split_title(raw_title)
    combined = f"{raw_title} {description}"
    # RawJob permits a missing location, but downstream filters and UI labels
    # treat it as text. Preserve the uncertainty explicitly instead of leaking
    # ``None`` into matching/search results.
    location = "Remote" if re.search(r"(?i)\bremote\b", combined) else "Unspecified"
    application_url = str(item.get("url") or "").strip() or f"https://news.ycombinator.com/item?id={item_id}"
    timestamp = item.get("time")
    discovered_at = (
        datetime.fromtimestamp(timestamp, tz=UTC)
        if isinstance(timestamp, int | float)
        else datetime.now(UTC)
    )
    return RawJob(
        source="hn",
        source_id=f"jobstory-{item_id}",
        company=company,
        title=title,
        location=location,
        employment_type=classify_employment_type(combined),
        seniority=classify_seniority(raw_title),
        description=description or raw_title,
        application_url=application_url,
        ats_type="hn",
        raw_data={
            "hn_feed": "jobstories",
            "hn_item_id": item_id,
            "hn_title": raw_title,
            "strict_pay": False,
        },
        discovered_at=discovered_at,
    )


def _split_title(raw_title: str) -> tuple[str, str]:
    match = _HIRING_RE.match(raw_title)
    if match:
        return match.group("company").strip(" -|"), match.group("title").strip(" -|")
    match = _AT_RE.match(raw_title)
    if match:
        return match.group("company").strip(" -|"), match.group("title").strip(" -|")
    # Long prose titles exist in the official feed. Use only the first
    # employer-shaped clause as company identity; the complete provider title
    # remains in raw_data and the job description for audit/search.
    prefix = re.split(r"\s*[|–—]\s*|[.!?]\s+|\s+is\s+", raw_title, maxsplit=1)[0]
    company = prefix.strip(" -|")[:200] or "Unknown startup"
    return company, raw_title[:300].rstrip()
