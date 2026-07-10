"""External job-description recovery for thin/stub postings.

Some postings redirect off-platform (company career site, Workday,
an ATS we don't scrape) and leave the stored posting with an empty or
near-empty description. This module makes a best-effort attempt to
fetch the real posting page and pull out the job description text so
requirement parsing and generation have something real to work with.

Every failure mode -- network error, non-200, non-HTML, too-short
extraction, or any unexpected exception -- returns ``None``. This is a
nice-to-have for a single selected job, never a hard dependency, so it
must not raise.
"""

from __future__ import annotations

import hashlib
import logging
import re
from html.parser import HTMLParser

import httpx

logger = logging.getLogger("autoapply.intake")

MIN_RECOVERED_CHARS = 300

# A realistic desktop-browser UA. Several career sites (Workday, Greenhouse
# embeds, generic company sites behind a CDN/WAF) 403 or serve a stripped
# no-JS shell to obvious bot user agents.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Boilerplate containers stripped (tag + contents) before extraction.
_STRIP_TAG_RE = re.compile(
    r"<(script|style|nav|header|footer|noscript|form)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Split points for the readability-lite heuristic: major block-level
# containers that career pages typically use to separate the JD body
# from surrounding chrome (sidebars, related-jobs rails, etc).
_BLOCK_SPLIT_RE = re.compile(
    r"<(?:div|section|article|main|aside|td|body)\b[^>]*>", re.IGNORECASE
)
_WHITESPACE_RE = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return _WHITESPACE_RE.sub(" ", " ".join(self._parts)).strip()


def _text_of(html_fragment: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html_fragment)
    except Exception:  # noqa: BLE001 -- malformed HTML must not raise
        pass
    return parser.get_text()


def _extract_main_text(html: str) -> str:
    """Readability-lite heuristic.

    Strips script/style/nav/header/footer/form (and their contents),
    then splits what remains on major block-level container tags and
    returns the largest contiguous text block -- i.e. the container
    most likely to be the actual job description rather than sidebar
    or chrome content that survived the tag strip.
    """
    cleaned = _COMMENT_RE.sub(" ", html)
    cleaned = _STRIP_TAG_RE.sub(" ", cleaned)

    chunks = _BLOCK_SPLIT_RE.split(cleaned)
    candidates = [_text_of(chunk) for chunk in chunks if chunk.strip()]
    # Fallback candidate: the whole (boilerplate-stripped) document, in
    # case the real content isn't wrapped in any of the split tags.
    candidates.append(_text_of(cleaned))
    candidates = [c for c in candidates if c]
    if not candidates:
        return ""
    return max(candidates, key=len)


def _recover_uncached(url: str, *, timeout: int) -> str | None:
    try:
        with httpx.Client(
            timeout=timeout, headers=DEFAULT_HEADERS, follow_redirects=True
        ) as client:
            response = client.get(url)
    except Exception as exc:  # noqa: BLE001 -- any network failure -> None
        logger.info("jd_recovery: fetch failed for %s (%s)", url, exc)
        return None

    if response.status_code != 200:
        logger.info(
            "jd_recovery: non-200 status %s for %s", response.status_code, url
        )
        return None

    content_type = response.headers.get("content-type", "")
    if content_type and "html" not in content_type.lower():
        logger.info(
            "jd_recovery: non-HTML content-type %r for %s", content_type, url
        )
        return None

    text = _extract_main_text(response.text)
    if len(text) < MIN_RECOVERED_CHARS:
        logger.info(
            "jd_recovery: extracted text too short (%d chars) for %s",
            len(text),
            url,
        )
        return None

    return text


def recover_job_description(url: str, *, timeout: int = 20) -> str | None:
    """Best-effort fetch + extract of a job description from ``url``.

    Never raises. Successful extractions are cached (namespace
    ``jd_recovery``, keyed by sha256 of the URL) so a retry doesn't
    refetch the same posting page.
    """
    if not url:
        return None

    try:
        from src.cache import get_cache  # noqa: PLC0415

        cache = get_cache()
    except Exception:  # noqa: BLE001 -- cache must never break recovery
        cache = None

    cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    if cache is not None:
        try:
            cached = cache.get("jd_recovery", cache_key)
        except Exception as exc:  # noqa: BLE001
            logger.debug("jd_recovery: cache lookup skipped (%s)", exc)
            cached = None
        if isinstance(cached, str) and cached:
            return cached

    try:
        text = _recover_uncached(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 -- recovery must never raise
        logger.info("jd_recovery: unexpected failure for %s (%s)", url, exc)
        return None

    if text is None:
        return None

    if cache is not None:
        try:
            cache.set("jd_recovery", cache_key, text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("jd_recovery: cache write skipped (%s)", exc)

    return text
