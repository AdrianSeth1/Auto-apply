"""Phase S4 / SUP-07: recover a full job description for a snippet-only posting.

Adzuna's search API returns a truncated description snippet and a
``redirect_url`` that Adzuna itself supplies (see ``src/intake/adzuna.py``).
Today nothing ever follows that URL, so every Adzuna posting is permanently
capped below Tier B by ``src/jobs/quality.py::assess_posting`` (it forces
``description_completeness="snippet"`` unless ``raw_data["full_jd_recovered"]``
is set) -- ``docs/JOB_SUPPLY_EXPANSION_PLAN.md`` Phase S4 calls this out as a
real supply-quality gap, not a bug: Adzuna adds breadth, not depth.

This module implements Phase S4's exact five-step contract, in order:

1. Follow *only* the provider-supplied ``application_url`` (never crawl or
   guess a different URL) -- a single bounded GET with redirects enabled.
2. Inspect the resolved URL. If it matches a known direct-ATS host shape
   (the same shapes ``src/jobs/quality.py::_DIRECT_ATS_HOSTS`` and each
   scraper's own ``application_url`` construction already use), extract the
   company slug and job id.
3. Reuse that adapter's real, already-tested scraper class to fetch the
   posting -- never hand-rolled HTML scraping of an arbitrary employer page.
   Only adapters marked ``enabled: true`` in ``config/source_policy.yaml``
   are ever reused; SmartRecruiters/Workable/Recruitee stay disabled pending
   their own Phase S3 conformance work (SUP-04/05/06), so recovering a
   snippet through one of them here would silently pre-empt that gate.
4. On success, return an updated ``RawJob`` carrying the full description and
   an honest ``raw_data["full_jd_recovered"] = True`` / recovery provenance.
   The caller is responsible for storing this as a **new immutable
   JobSnapshot** (never mutating the original) -- see
   ``src/application/resolve_snippets.py``.
5. Rescoring is not triggered from here at all. ``JobTargetEvaluation`` is
   keyed by ``snapshot_id`` (``src/matching/evaluation_store.py::persist_evaluation``),
   so a brand-new snapshot simply has no existing evaluation row and gets a
   fresh one the next time the normal portfolio run evaluates it -- "rescore
   only after recovery succeeds" falls out of the existing immutable-ledger
   design for free, with no special-case wiring needed here.

Deliberately out of scope, not attempted, never silently guessed at:

- **Workday.** Its own docstring (``src/intake/workday.py``) documents that
  Workday careers pages are client-rendered SPAs where a plain HTTP GET
  "mostly returns an empty shell" -- a bounded static follow would recover
  nothing and risks a misleading ``full_jd_recovered: True`` on empty
  content. Left as a documented follow-up, not solved with a browser-render
  fallback here (Phase S4 explicitly forbids browser-scraping the broad
  funnel: "Do not browser-scrape arbitrary pages in the broad funnel.").
- **SmartRecruiters / Workable / Recruitee.** Real scraper classes exist but
  are ``enabled: false`` in ``config/source_policy.yaml`` pending Phase S3
  conformance tests. This resolver checks that flag and returns ``None``
  rather than reusing a not-yet-conformance-tested adapter.
- Any resolved URL that isn't a recognized direct-ATS shape. Per the plan:
  "Unresolved snippets remain searchable/auditable but cannot create review
  cards or materials" -- returning ``None`` and leaving the posting exactly
  as it was is the correct, final outcome, not a gap to patch.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from src.intake.ashby import AshbyScraper
from src.intake.base import DEFAULT_HEADERS, ScraperError
from src.intake.greenhouse import GreenhouseScraper
from src.intake.lever import LeverScraper
from src.intake.schema import ApplicationTargetV2, JobProvenanceV2, RawJob

logger = logging.getLogger("autoapply.intake.full_jd_resolver")

DEFAULT_RESOLVE_TIMEOUT = 20

_FULL_DESCRIPTION_MIN_CHARS = 300

# Only adapters this resolver knows how to reuse safely. Workday is
# deliberately absent (see module docstring); SmartRecruiters/Workable/
# Recruitee are present so a match can be recognized and then correctly
# rejected by the source_policy enabled-check, rather than falling through
# to "no adapter recognized" (which would be a misleading audit signal).
_URL_PATTERNS: dict[str, re.Pattern[str]] = {
    "greenhouse": re.compile(
        r"boards\.greenhouse\.io/(?:embed/job_app\?for=)?(?P<slug>[\w-]+)/jobs/(?P<job_id>\d+)",
        re.IGNORECASE,
    ),
    "lever": re.compile(
        r"jobs\.lever\.co/(?P<slug>[\w-]+)/(?P<job_id>[0-9a-fA-F-]{8,})",
        re.IGNORECASE,
    ),
    "ashby": re.compile(
        r"jobs\.ashbyhq\.com/(?P<slug>[\w-]+)/(?P<job_id>[0-9a-fA-F-]{8,})",
        re.IGNORECASE,
    ),
    "smartrecruiters": re.compile(
        r"jobs\.smartrecruiters\.com/(?:[\w-]+/)?(?P<slug>[\w-]+)/(?P<job_id>[\w-]+)",
        re.IGNORECASE,
    ),
    "workable": re.compile(
        r"apply\.workable\.com/(?P<slug>[\w-]+)/j/(?P<job_id>[\w-]+)",
        re.IGNORECASE,
    ),
    "recruitee": re.compile(
        r"(?P<slug>[\w-]+)\.recruitee\.com/o/(?P<job_id>[\w-]+)",
        re.IGNORECASE,
    ),
}

# The three adapters this resolver actually knows how to re-fetch a single
# posting through. Kept separate from _URL_PATTERNS so an unsupported-but-
# recognized adapter (matched host, but no reuse path yet, e.g. Workday) is
# still distinguishable in logs/audits from "recognized nothing at all".
_REFETCHABLE_ADAPTERS = frozenset({"greenhouse", "lever", "ashby"})


@dataclass(frozen=True)
class ResolveOutcome:
    """Result of one resolution attempt, always returned -- never raised."""

    resolved: bool
    job: RawJob | None = None
    adapter: str | None = None
    resolved_url: str | None = None
    reason: str | None = None  # populated when resolved is False


def _match_adapter(resolved_url: str) -> tuple[str, str, str] | None:
    """Return (adapter, slug, job_id) for the first recognized ATS URL shape."""
    for adapter, pattern in _URL_PATTERNS.items():
        m = pattern.search(resolved_url)
        if m:
            return adapter, m.group("slug"), m.group("job_id")
    return None


def _follow_application_url(
    application_url: str, *, timeout: int
) -> tuple[str, str] | tuple[None, None]:
    """GET the provider-supplied URL and return (resolved_url, final_html).

    Follows redirects (this URL came from the provider itself -- Adzuna's
    own ``redirect_url`` field -- so following it is not "crawling an
    arbitrary page", it's completing the one hop the provider already
    pointed us at). Returns ``(None, None)`` on any network failure; never
    raises.
    """
    try:
        with httpx.Client(
            timeout=timeout, headers=DEFAULT_HEADERS, follow_redirects=True
        ) as client:
            resp = client.get(application_url)
            return str(resp.url), resp.text
    except httpx.HTTPError as exc:
        logger.info("full_jd_resolver: could not follow %r: %s", application_url, exc)
        return None, None


def resolve_full_jd(
    job: RawJob,
    *,
    source_policy: dict | None = None,
    timeout: int = DEFAULT_RESOLVE_TIMEOUT,
) -> ResolveOutcome:
    """Attempt to recover a full description for one snippet-only posting.

    Args:
        job: The current ``RawJob`` (typically freshly parsed from Adzuna,
            or reloaded from a persisted snippet-only snapshot).
        source_policy: Parsed ``config/source_policy.yaml`` (or an
            equivalent dict). Adapters missing or not ``enabled: true`` are
            never reused. Defaults to "nothing enabled" (fail closed) if
            omitted, so a caller can never accidentally bypass the policy
            gate by forgetting the argument.
        timeout: Per-request timeout in seconds for both the initial
            redirect-follow and the adapter re-fetch.

    Returns:
        A ``ResolveOutcome``. ``resolved=False`` covers every non-recovery
        case (not a snippet, no application_url, unresolvable/unsupported
        target, disabled adapter, network/parse failure) with a ``reason``
        string for audit logging -- this function never raises.
    """
    completeness = str((job.raw_data or {}).get("description_completeness") or "")
    if (job.source or "").strip().lower() != "adzuna" or completeness != "snippet":
        return ResolveOutcome(resolved=False, reason="not_a_snippet_posting")

    if (job.raw_data or {}).get("full_jd_recovered"):
        return ResolveOutcome(resolved=False, reason="already_recovered")

    application_url = (job.application_url or "").strip()
    if not application_url:
        return ResolveOutcome(resolved=False, reason="missing_application_url")

    resolved_url, _html = _follow_application_url(application_url, timeout=timeout)
    if not resolved_url:
        return ResolveOutcome(resolved=False, reason="redirect_follow_failed")

    match = _match_adapter(resolved_url)
    if not match:
        return ResolveOutcome(
            resolved=False, resolved_url=resolved_url, reason="unrecognized_target"
        )
    adapter, slug, job_id = match

    if adapter not in _REFETCHABLE_ADAPTERS:
        return ResolveOutcome(
            resolved=False,
            adapter=adapter,
            resolved_url=resolved_url,
            reason="adapter_not_refetchable",
        )

    policy = source_policy or {}
    adapter_policy = (policy.get("adapters") or {}).get(adapter) or {}
    if not adapter_policy.get("enabled"):
        return ResolveOutcome(
            resolved=False,
            adapter=adapter,
            resolved_url=resolved_url,
            reason="adapter_disabled_in_source_policy",
        )

    try:
        recovered = _fetch_via_adapter(adapter, slug, job_id)
    except ScraperError as exc:
        logger.info(
            "full_jd_resolver: %s re-fetch failed for %s/%s: %s", adapter, slug, job_id, exc
        )
        return ResolveOutcome(
            resolved=False, adapter=adapter, resolved_url=resolved_url, reason="scraper_error"
        )

    if recovered is None:
        return ResolveOutcome(
            resolved=False, adapter=adapter, resolved_url=resolved_url, reason="job_not_found"
        )

    description = (recovered.description or "").strip()
    if not description:
        return ResolveOutcome(
            resolved=False,
            adapter=adapter,
            resolved_url=resolved_url,
            reason="recovered_description_empty",
        )

    new_completeness = "full" if len(description) >= _FULL_DESCRIPTION_MIN_CHARS else "partial"
    updated_raw_data = {
        **(job.raw_data or {}),
        "description_completeness": new_completeness,
        "full_jd_recovered": True,
        "full_jd_source_adapter": adapter,
        "full_jd_resolved_url": resolved_url,
    }
    updated_job = job.model_copy(
        update={
            "description": description,
            "raw_data": updated_raw_data,
            "provenance": JobProvenanceV2(
                adapter="adzuna",
                channel="aggregator",
                listing_url=job.application_url,
                publisher_relationship="third_party_aggregator",
                description_completeness=new_completeness,  # type: ignore[arg-type]
                application_target=ApplicationTargetV2(
                    original_url=job.application_url,
                    resolved_url=resolved_url,
                    kind="direct_ats",
                    resolution_status="resolved_via_adapter",
                ),
                parser_confidence=0.8,
            ),
        }
    )
    return ResolveOutcome(
        resolved=True, job=updated_job, adapter=adapter, resolved_url=resolved_url
    )


def _fetch_via_adapter(adapter: str, slug: str, job_id: str) -> RawJob | None:
    """Reuse the real scraper class for one adapter to fetch a single posting.

    Greenhouse and Lever both expose a ``fetch_job(slug, id)`` single-posting
    method already (used elsewhere, not written for this ticket). Ashby has
    no single-posting endpoint in this codebase, so it reuses ``fetch_jobs``
    (the same call the live board fetch already makes) and finds the
    matching id -- a single company's board, not an arbitrary crawl.
    Returns ``None`` if the id isn't present in the adapter's response
    (distinct from a network/parse failure, which raises ``ScraperError``
    and is handled by the caller).
    """
    if adapter == "greenhouse":
        with GreenhouseScraper() as scraper:
            return scraper.fetch_job(slug, job_id)
    if adapter == "lever":
        with LeverScraper() as scraper:
            return scraper.fetch_job(slug, job_id)
    if adapter == "ashby":
        with AshbyScraper() as scraper:
            jobs = scraper.fetch_jobs(slug)
        return next((j for j in jobs if j.source_id == job_id), None)
    raise AssertionError(f"unreachable: {adapter} is not in _REFETCHABLE_ADAPTERS")


__all__ = ["ResolveOutcome", "resolve_full_jd"]
