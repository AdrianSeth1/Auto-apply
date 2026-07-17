"""Conservative, explainable cross-source job identity helpers.

This module never merges or deletes jobs.  It produces a stable fingerprint
used to *suggest* that records from different sources represent the same role.
Exact normalized company and title are required; location or canonical apply
URL supplies the corroborating signal.  Keeping this deterministic makes the
result easy to audit and easy for other agents (including Claude) to extend.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_LEGAL_SUFFIXES = re.compile(
    r"\b(incorporated|inc|llc|ltd|limited|corp|corporation|company|co)\b", re.I
)
_NON_WORD = re.compile(r"[^a-z0-9]+")
_TRACKING_KEYS = {
    "gh_src",
    "gh_jid",
    "source",
    "ref",
    "referrer",
    "utm_source",
    "utm_medium",
    "utm_campaign",
}


def normalize_identity_text(value: str | None) -> str:
    """Lowercase and collapse punctuation/whitespace for identity matching."""
    text = _LEGAL_SUFFIXES.sub(" ", (value or "").lower())
    return _NON_WORD.sub(" ", text).strip()


def canonicalize_application_url(value: str | None) -> str:
    """Remove fragments and tracking parameters without changing destination."""
    if not value:
        return ""
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    query = urlencode(
        sorted(
            (key, val)
            for key, val in parse_qsl(parts.query)
            if key.lower() not in _TRACKING_KEYS
        )
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def canonical_fingerprint(
    *, company: str | None, title: str | None, location: str | None, application_url: str | None
) -> str | None:
    """Return a cluster key only when company/title plus corroboration are present."""
    company_key = normalize_identity_text(company)
    title_key = normalize_identity_text(title)
    location_key = normalize_identity_text(location)
    url_key = canonicalize_application_url(application_url)
    if not company_key or not title_key or not (location_key or url_key):
        return None
    # Location is deliberately preferred: aggregator and employer ATS URLs
    # differ for the same role, while exact normalized company+title+location
    # is a conservative cross-source signal. URL is the fallback for postings
    # whose location is absent.
    corroborator = f"location:{location_key}" if location_key else f"url:{url_key}"
    raw = f"{company_key}|{title_key}|{corroborator}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
