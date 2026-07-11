"""Hacker News "Who is hiring?" monthly thread adapter.

Uses the HN Algolia Search API (keyless, public):
  1. GET https://hn.algolia.com/api/v1/search_by_date
       ?tags=story,author_whoishiring&query=Who%20is%20hiring
     to find the latest official thread. Client-side title filter
     required -- the raw query alone (verified live, 2026-07-11) also
     matches the sibling "Who wants to be hired?" thread (Algolia's
     relevance/stemming treats "hiring" and "hired" as close enough).
  2. GET https://hn.algolia.com/api/v1/items/{thread_id}
     for the full thread, including every top-level comment nested
     under "children" (each comment's OWN "children" are replies, not
     postings -- not recursed into).

Comment format is free text, not structured data, so parsing below is
best-effort and was calibrated against a real live thread (262 top-level
comments, July 2026) rather than assumed:
  - Header line = text before the comment's first ``<p>`` tag (or the
    whole comment when there's no ``<p>`` at all). Split into segments
    on ``|`` (any spacing) or on a hyphen/en-dash/em-dash that has
    whitespace on BOTH sides -- the latter guard is required so
    "Full-Stack" (no surrounding spaces) doesn't get mistaken for a
    "Company - Location" style separator.
  - Field ORDER within the header is not standardized across posters
    (some lead with location, others with company), so "company" here
    is a best-effort first-segment guess, exactly as specified, not a
    guaranteed-correct field.
  - A segment containing "remote" (case-insensitive) is used as
    ``location`` when present; no attempt is made to guess a location
    from non-"remote" segments (unknown stays unknown, same convention
    used throughout this codebase, rather than a low-confidence guess).
  - The first ``href="..."`` anywhere in the comment (HN auto-linkifies
    plain-text URLs server-side, confirmed live) becomes
    ``application_url``; falls back to the comment's own HN permalink
    when the poster included no link at all (observed on ~16% of a
    real thread's postings).
  - Comments under ``MIN_COMMENT_CHARS`` are skipped as noise (meta
    commentary, not postings -- e.g. "don't waste your time with these
    guys" was a real top-level comment in the sampled thread). The
    threshold was picked to sit safely between that kind of one-line
    remark (~37 chars) and the shortest genuine posting observed live
    (~72 chars).

``raw_data["strict_pay"] = True`` is set on every job here -- see the
startup quality gate in ``src.application.jobs._apply_search_filters``:
unlike every other source, a HN posting with NO stated compensation is
DROPPED (not passed through) when the caller has a pay filter active.
"""

from __future__ import annotations

import html
import logging
import re

import httpx

from src.intake.base import DEFAULT_HEADERS, DEFAULT_TIMEOUT, ScraperError
from src.intake.html_utils import strip_html
from src.intake.schema import RawJob, classify_employment_type, classify_seniority

logger = logging.getLogger("autoapply.intake.hn_hiring")

SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
ITEM_URL = "https://hn.algolia.com/api/v1/items/{id}"
THREAD_TITLE_RE = re.compile(r"^ask hn:\s*who is hiring\b", re.IGNORECASE)

MIN_COMMENT_CHARS = 50
MAX_TITLE_CHARS = 200

_PIPE_SPLIT_RE = re.compile(r"\s*\|\s*")
_DASH_SPLIT_RE = re.compile(r"\s[-–—]\s")  # -, en-dash, em-dash; whitespace-bounded
_HREF_RE = re.compile(r'href="([^"]+)"')


def find_latest_thread_id(*, timeout: int = DEFAULT_TIMEOUT) -> str | None:
    """Find the most recent official "Who is hiring?" thread's HN item id."""
    params = {
        "tags": "story,author_whoishiring",
        "query": "Who is hiring",
        "hitsPerPage": 10,
    }
    try:
        with httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS) as client:
            resp = client.get(SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        raise ScraperError(f"HN thread search failed: {e}") from e

    if not isinstance(data, dict):
        raise ScraperError("Unexpected HN Algolia search response shape")
    hits = data.get("hits")
    if not isinstance(hits, list):
        raise ScraperError("Unexpected HN Algolia search response shape")

    for hit in hits:
        title = hit.get("title") or ""
        if THREAD_TITLE_RE.match(title.strip()):
            return hit.get("objectID")
    return None


def fetch_latest_hn_hiring_jobs(
    *, force_refresh: bool = False, timeout: int = DEFAULT_TIMEOUT
) -> list[RawJob]:
    """Fetch every posting in the latest "Who is hiring?" thread.

    Caches the parsed thread in the same in-process board cache used by
    the ATS board scrapers (src.intake.search), keyed ("hn", thread_id,
    False), so repeated searches within the TTL window don't re-fetch
    and re-parse all ~250+ comments.
    """
    thread_id = find_latest_thread_id(timeout=timeout)
    if thread_id is None:
        raise ScraperError("No 'Who is hiring?' thread found via HN Algolia search")

    from src.intake.search import _board_cache_get, _board_cache_put  # noqa: PLC0415

    cache_key = ("hn", str(thread_id), False)
    if not force_refresh:
        cached = _board_cache_get(cache_key)
        if cached is not None:
            logger.info("[hn/%s] board cache hit (%d jobs)", thread_id, len(cached))
            return cached

    jobs = _fetch_thread_jobs(thread_id, timeout=timeout)
    _board_cache_put(cache_key, jobs)
    return jobs


def _fetch_thread_jobs(thread_id: str, *, timeout: int = DEFAULT_TIMEOUT) -> list[RawJob]:
    url = ITEM_URL.format(id=thread_id)
    try:
        with httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        raise ScraperError(f"Failed to fetch HN thread {thread_id}: {e}") from e

    if not isinstance(data, dict):
        raise ScraperError(f"Unexpected HN thread response shape for {thread_id}")
    comments = data.get("children")
    if not isinstance(comments, list):
        raise ScraperError(f"Unexpected HN thread response shape for {thread_id}")

    jobs: list[RawJob] = []
    for comment in comments:
        try:
            job = _parse_comment(comment, thread_id)
        except Exception as e:
            logger.warning(
                "Skipping malformed HN comment %s: %s", comment.get("id"), e
            )
            continue
        if job is not None:
            jobs.append(job)

    logger.info("Fetched %d jobs from HN thread %s", len(jobs), thread_id)
    return jobs


def _parse_comment(comment: dict, thread_id: str) -> RawJob | None:
    raw_html = comment.get("text") or ""
    if not raw_html:
        return None  # deleted/dead comment

    description = strip_html(raw_html)
    if len(description) < MIN_COMMENT_CHARS:
        return None  # noise (meta commentary, not a posting)

    header_raw = raw_html.split("<p>", 1)[0]
    header_text = strip_html(header_raw).strip()
    if not header_text:
        return None

    segments = _split_header(header_text)
    company = (segments[0] if segments else header_text).strip() or "Unknown"
    title = (
        header_text
        if len(header_text) <= MAX_TITLE_CHARS
        else header_text[:MAX_TITLE_CHARS] + "..."
    )

    location = None
    for segment in segments:
        if "remote" in segment.lower():
            location = segment
            break

    href_match = _HREF_RE.search(raw_html)
    comment_id = comment["id"]
    permalink = f"https://news.ycombinator.com/item?id={comment_id}"
    application_url = html.unescape(href_match.group(1)) if href_match else permalink

    employment_hint = f"{header_text} {title}"
    employment_type = classify_employment_type(employment_hint)
    seniority = classify_seniority(title)

    # raw_data = dict(comment) already carries the comment's own
    # "created_at" (ISO string, e.g. "2026-07-01T15:01:52.000Z") through
    # verbatim -- scorer._posting_age_days picks it up as a ghost-age
    # candidate, same pattern as every other adapter (no reformatting
    # needed; _parse_posting_datetime already handles this ISO shape).
    raw_data = dict(comment)
    raw_data["strict_pay"] = True
    raw_data["hn_thread_id"] = thread_id

    return RawJob(
        source="hn",
        source_id=str(comment_id),
        company=company,
        title=title,
        location=location,
        employment_type=employment_type,
        seniority=seniority,
        description=description,
        application_url=application_url,
        ats_type="unknown",
        raw_data=raw_data,
    )


def _split_header(header: str) -> list[str]:
    if "|" in header:
        return [seg.strip() for seg in _PIPE_SPLIT_RE.split(header) if seg.strip()]
    parts = [seg.strip() for seg in _DASH_SPLIT_RE.split(header) if seg.strip()]
    return parts if len(parts) > 1 else [header.strip()]
