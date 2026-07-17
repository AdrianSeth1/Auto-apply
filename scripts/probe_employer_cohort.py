"""SUP-02: probe the 35-employer research cohort for real, verifiable endpoints.

Read-only against the rest of the system: this script never writes to
``config/companies.yaml`` or ``config/employer_cohort.v1.yaml``, and it never
sets ``enabled: true`` on anything. For each employer in
``config/employer_cohort.v1.yaml`` it:

  1. fetches the official ``careers_url`` (following redirects);
  2. scans the resolved URL + page HTML for Greenhouse, Lever, Ashby,
     Workday, SmartRecruiters, Workable, or Recruitee link patterns. When
     the static HTML has nothing (common for JS-rendered React/Next/
     Webflow careers pages), falls back to a single lazily-launched,
     run-scoped headless Chromium render (``BrowserRenderer``) and re-scans
     the rendered HTML -- disable with ``--no-js-render`` if Playwright's
     browser binaries aren't installed (``playwright install chromium``);
  3. records the exact endpoint configuration it found, how it was found
     (static HTML vs. JS render), and today's date as
     the evidence date;
  4. fetches a small sample through the REAL production scraper class for
     that adapter (``src.intake.greenhouse.GreenhouseScraper`` etc.) -- no
     separate/duplicate fetch logic;
  5. verifies employer identity, non-empty unique IDs, direct (non-redirect)
     application URLs, at least one non-empty location, and at least one
     full (>=300 char) description;
  6. writes three artifacts under ``data/audits/`` -- a full audit (pass AND
     fail, every employer, so failures stay visible for review), a proposed
     ``companies.yaml`` patch (verified endpoints only), and a proposed
     cohort ``verification_status`` patch. None of these are applied
     automatically;
  7. never enables or imports anything itself. A verified probe only
     proposes ``verification_status: verified_probe_passed`` --
     ``scripts/validate_employer_cohort.py`` still requires a separate,
     explicit human step to set ``verification_status: verified_approved``
     and ``enabled: true`` before ``scripts/import_companies_registry.py``
     or the cohort validator will accept it.

This script makes real outbound HTTP requests to each employer's public
careers page and ATS API. It is meant to be run by Arya on a machine with
normal internet access:

    uv run python scripts/probe_employer_cohort.py
    uv run python scripts/probe_employer_cohort.py --only "Retool,Drata"
    uv run python scripts/probe_employer_cohort.py --sample-size 5
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml

from src.core.config import PROJECT_ROOT
from src.intake.ashby import AshbyScraper
from src.intake.base import DEFAULT_HEADERS, BaseScraper, ScraperError
from src.intake.greenhouse import GreenhouseScraper
from src.intake.lever import LeverScraper
from src.intake.recruitee import RecruiteeScraper
from src.intake.schema import RawJob
from src.intake.smartrecruiters import SmartRecruitersScraper
from src.intake.workable import WorkablePublicScraper
from src.intake.workday import WorkdayScraper
from src.matching.target_schema import normalize_phrase

# ---------------------------------------------------------------------------
# Adapter detection
# ---------------------------------------------------------------------------

# Patterns mirror each scraper's own listing/apply-URL construction
# (see src/intake/board_discovery.py for the greenhouse/lever/ashby
# precedent, and each adapter module's _parse_job for its own URL shape).
_ADAPTER_PATTERNS: dict[str, re.Pattern[str]] = {
    "greenhouse": re.compile(
        r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9][a-z0-9_-]*)",
        re.IGNORECASE,
    ),
    "lever": re.compile(r"jobs\.lever\.co/([A-Za-z0-9][A-Za-z0-9_-]*)", re.IGNORECASE),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9][A-Za-z0-9_-]*)", re.IGNORECASE),
    "smartrecruiters": re.compile(
        r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9][A-Za-z0-9_-]*)", re.IGNORECASE
    ),
    "workable": re.compile(r"apply\.workable\.com/([a-z0-9][a-z0-9_-]*)", re.IGNORECASE),
    "recruitee": re.compile(r"\b([a-z0-9-]+)\.recruitee\.com", re.IGNORECASE),
}
# React/Next/etc. careers pages frequently call the ATS's JSON API directly
# via client-side fetch() rather than rendering a plain <a href> link -- the
# slug never appears in the DOM at all, only in the network request URL
# (see the js_render fallback's network_urls capture below). These patterns
# match each adapter's REAL API call shape, confirmed against each
# scraper's own fetch URL in src/intake/*.py (not guessed):
#   greenhouse -> boards-api.greenhouse.io/v1/boards/{slug}
#   lever      -> api.lever.co/v0/postings/{slug}
#   ashby      -> api.ashbyhq.com/posting-api/job-board/{slug}
#   smartrecruiters -> api.smartrecruiters.com/v1/companies/{slug}
#   workable   -> apply.workable.com/api/v3/accounts/{slug} (same host as
#                 the public link pattern above, different path -- this is
#                 also why "api"/"accounts"/version segments are excluded
#                 in _NOT_SLUGS, so the public-link pattern can't
#                 misidentify this same URL's "api" path segment as a slug)
_API_PATTERNS: dict[str, re.Pattern[str]] = {
    "greenhouse": re.compile(r"boards-api\.greenhouse\.io/v1/boards/([a-z0-9][a-z0-9_-]*)", re.IGNORECASE),
    "lever": re.compile(r"api\.lever\.co/v0/postings/([A-Za-z0-9][A-Za-z0-9_-]*)", re.IGNORECASE),
    "ashby": re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9][A-Za-z0-9_-]*)", re.IGNORECASE),
    "smartrecruiters": re.compile(r"api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9][A-Za-z0-9_-]*)", re.IGNORECASE),
    "workable": re.compile(r"apply\.workable\.com/api/v\d+/accounts/([a-z0-9][a-z0-9_-]*)", re.IGNORECASE),
}
# {tenant}.{host}.myworkdayjobs.com[/xx-XX]/{site} -- the "site" segment is
# an arbitrary per-tenant label (see src/intake/workday.py docstring) and is
# NOT always present in a plain link; when it's missing we can detect
# "this is a Workday tenant" but cannot build a fetchable endpoint from it.
_WORKDAY_RE = re.compile(
    r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com(?:/(?:[a-z]{2}-[a-z]{2}/)?([A-Za-z0-9_]+))?",
    re.IGNORECASE,
)
# The real CXS API call shape (src/intake/workday.py): .../wday/cxs/{tenant}/{site}/jobs.
# Distinct from _WORKDAY_RE's plain-page-link shape -- "wday"/"cxs" would
# otherwise get misread as the "site" segment by the generic pattern above.
_WORKDAY_CXS_RE = re.compile(
    r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/wday/cxs/[a-z0-9-]+/([A-Za-z0-9_]+)/jobs",
    re.IGNORECASE,
)
_NOT_SLUGS = {"embed", "job_app", "jobs", "careers", "www", "api", "accounts", "v0", "v1", "v2", "v3", "wday", "cxs"}

_SCRAPER_CLASSES: dict[str, type[BaseScraper]] = {
    "greenhouse": GreenhouseScraper,
    "lever": LeverScraper,
    "ashby": AshbyScraper,
    "workday": WorkdayScraper,
    "smartrecruiters": SmartRecruitersScraper,
    "workable": WorkablePublicScraper,
    "recruitee": RecruiteeScraper,
}

_APPLICATION_HOSTS: dict[str, tuple[str, ...]] = {
    "greenhouse": ("greenhouse.io",),
    "lever": ("lever.co",),
    "ashby": ("ashbyhq.com",),
    "workday": ("myworkdayjobs.com",),
    "smartrecruiters": ("smartrecruiters.com",),
    "workable": ("workable.com",),
    "recruitee": ("recruitee.com",),
}

_FULL_DESCRIPTION_MIN_CHARS = 300  # matches the "full" convention used by JobProvenanceV2 elsewhere


@dataclass
class AdapterCandidate:
    adapter: str
    endpoint_config: str | dict | None  # None only for an incomplete Workday match (site unresolved)
    occurrences: int
    detection_method: str = "static"  # "static" (plain HTML) or "js_render" (headless-browser fallback)


def detect_adapter_candidates(
    final_url: str, html: str, *, detection_method: str = "static"
) -> list[AdapterCandidate]:
    """Scan the resolved careers-page URL + HTML body for known ATS links.

    Returns every distinct candidate found (a careers page can legitimately
    reference more than one board), most-referenced first. Never guesses --
    an unresolved Workday tenant (no "site" segment) is still recorded, with
    ``endpoint_config=None``, so it stays visible for manual review instead
    of silently disappearing. ``detection_method`` just tags where the HTML
    being scanned came from (plain fetch vs. ``BrowserRenderer`` fallback)
    so the audit stays honest about how each candidate was found -- it
    doesn't change matching behavior.
    """
    counts: dict[tuple[str, str], int] = {}
    configs: dict[tuple[str, str], str | dict | None] = {}
    for text in (final_url, html):
        if not text:
            continue
        # Two separate pattern dicts, not merged -- _ADAPTER_PATTERNS and
        # _API_PATTERNS share adapter-name keys (e.g. both have
        # "greenhouse"), so a dict merge would silently drop one pattern
        # per adapter on the key collision instead of scanning for both.
        for pattern_group in (_ADAPTER_PATTERNS, _API_PATTERNS):
            for adapter, pattern in pattern_group.items():
                for match in pattern.finditer(text):
                    slug = match.group(1)
                    if slug.lower() in _NOT_SLUGS:
                        continue
                    key = (adapter, slug.lower())
                    counts[key] = counts.get(key, 0) + 1
                    configs[key] = slug

        # The real CXS API call shape first -- it always carries a
        # trustworthy site segment, unlike the generic page-link pattern.
        for match in _WORKDAY_CXS_RE.finditer(text):
            tenant, host, site = match.group(1), match.group(2), match.group(3)
            key = ("workday", f"{tenant.lower()}|{host.lower()}|{site}")
            counts[key] = counts.get(key, 0) + 1
            configs[key] = {"tenant": tenant.lower(), "host": host.lower(), "site": site}

        for match in _WORKDAY_RE.finditer(text):
            tenant, host, site = match.group(1), match.group(2), match.group(3)
            # The generic pattern's optional trailing segment can pick up
            # "wday" (from an unrelated CXS API URL matching this looser
            # pattern too) -- never trust that as a real site name.
            if site and site.lower() in _NOT_SLUGS:
                site = None
            key = ("workday", f"{tenant.lower()}|{host.lower()}|{site or ''}")
            counts[key] = counts.get(key, 0) + 1
            configs[key] = (
                {"tenant": tenant.lower(), "host": host.lower(), "site": site} if site else None
            )

    candidates = [
        AdapterCandidate(
            adapter=adapter,
            endpoint_config=configs[(adapter, config_key)],
            occurrences=count,
            detection_method=detection_method,
        )
        for (adapter, config_key), count in counts.items()
    ]
    candidates.sort(key=lambda c: c.occurrences, reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# JS-render fallback (many modern careers pages inject their ATS widget
# client-side -- a plain httpx GET never sees it, only the resolved shell)
# ---------------------------------------------------------------------------


_JS_SETTLE_MS = 2500  # fixed pause after domcontentloaded for client JS to render the ATS widget


@dataclass
class RenderResult:
    html: str
    final_url: str | None
    error: str | None
    # URLs of every network request the page made while loading. Many React/
    # Next careers pages fetch the ATS's JSON API directly via client-side
    # fetch() and never render a plain <a href> link at all -- the slug only
    # ever appears in one of these request URLs, never in page.content().
    network_urls: list[str] = field(default_factory=list)


class BrowserRenderer:
    """Lazy, run-scoped headless Chromium, used only when static-HTML
    adapter detection finds nothing for an employer.

    One browser instance is reused across the whole cohort run instead of
    launched per employer -- starting Chromium is the expensive part, not
    rendering one more page in an already-running instance. It is also
    lazy: an employer whose static HTML already contains a usable ATS link
    never triggers a browser launch at all, so a run where every employer
    resolves statically pays nothing extra.

    Deliberately does NOT wait for Playwright's "networkidle" -- modern
    marketing sites routinely keep a connection open forever (chat widgets,
    analytics beacons, websockets), so networkidle reliably burns the full
    timeout on exactly the pages this fallback targets. Waiting for
    "domcontentloaded" then a short fixed settle pause is far faster and
    still gives client-side JS (React/Next/Webflow) time to inject its ATS
    widget into the DOM.

    Never raises: a missing Playwright install or a broken/crashed browser
    must degrade to "render unavailable" for whichever employers needed it,
    not crash the whole 35-employer probe run over one bad render.
    """

    def __init__(self, *, timeout_ms: int = 20000) -> None:
        self._timeout_ms = timeout_ms
        self._playwright = None
        self._browser = None
        self._unavailable_reason: str | None = None

    def __enter__(self) -> "BrowserRenderer":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:  # noqa: BLE001 -- best-effort cleanup only
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:  # noqa: BLE001 -- best-effort cleanup only
                pass
            self._playwright = None

    def _ensure_started(self) -> str | None:
        """Returns an error string if the browser isn't available, else None."""
        if self._browser is not None:
            return None
        if self._unavailable_reason is not None:
            return self._unavailable_reason
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            self._unavailable_reason = f"playwright not installed: {exc}"
            return self._unavailable_reason
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001 -- a broken browser install must not crash the probe run
            self._unavailable_reason = f"{type(exc).__name__}: {exc}"
            return self._unavailable_reason
        return None

    def render(self, url: str) -> RenderResult:
        error = self._ensure_started()
        if error is not None:
            return RenderResult(html="", final_url=None, error=error)
        try:
            page = self._browser.new_page(user_agent=DEFAULT_HEADERS["User-Agent"])
            network_urls: list[str] = []
            page.on("request", lambda request: network_urls.append(request.url))
            try:
                page.goto(url, timeout=self._timeout_ms, wait_until="domcontentloaded")
                page.wait_for_timeout(_JS_SETTLE_MS)
                html = page.content()
                final_url = page.url
            finally:
                page.close()
            return RenderResult(html=html, final_url=final_url, error=None, network_urls=network_urls)
        except Exception as exc:  # noqa: BLE001 -- one bad render must not crash the probe run
            return RenderResult(html="", final_url=None, error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Real-adapter sample fetch
# ---------------------------------------------------------------------------


def fetch_sample(
    adapter: str,
    endpoint_config: str | dict,
    *,
    timeout: int = 30,
    sample_size: int | None = None,
) -> tuple[list[RawJob], str | None]:
    """Fetch jobs through the real production scraper for ``adapter``.

    Uses the exact same scraper classes ``src.intake.search`` uses in live
    discovery -- no separate/duplicate fetch logic (req #4). Never raises:
    probing 35 employers must survive one bad slug guess and keep going, so
    any failure comes back as ``(jobs, error_message)`` instead.

    Workday's list endpoint carries no description field at all (see
    ``src/intake/workday.py``'s module docstring) -- the real pipeline
    recovers it with a separate ``fetch_job_detail`` call per job. Doing the
    same here, bounded to the sample actually being verified, is not
    optional: without it every Workday candidate fails
    ``has_full_description`` regardless of how good the endpoint is, which
    isn't a fact about the endpoint, it's an artifact of skipping a known
    required step.
    """
    scraper_class = _SCRAPER_CLASSES.get(adapter)
    if scraper_class is None:
        return [], f"no scraper registered for adapter {adapter!r}"
    try:
        with scraper_class(timeout=timeout) as scraper:
            jobs = scraper.fetch_jobs(endpoint_config)
            sample = jobs[:sample_size] if sample_size is not None else jobs
            if adapter == "workday" and hasattr(scraper, "fetch_job_detail"):
                sample = [scraper.fetch_job_detail(job) for job in sample]
        return sample, None
    except ScraperError as exc:
        return [], str(exc)
    except Exception as exc:  # noqa: BLE001 -- a bad endpoint guess must not crash the whole probe run
        return [], f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Verification (SUP-02 req #5)
# ---------------------------------------------------------------------------


def _identity_match(employer_name: str, company_field: str | None) -> bool:
    name_norm = normalize_phrase(employer_name)
    company_norm = normalize_phrase(company_field or "")
    if not name_norm or not company_norm:
        return False
    if set(name_norm.split()) & set(company_norm.split()):
        return True
    # Loose fallback for slug-derived company fields with no spaces
    # (e.g. a fallback "Modernhealth" vs cohort name "Modern Health").
    name_compact = name_norm.replace(" ", "")
    company_compact = company_norm.replace(" ", "")
    return bool(name_compact) and (name_compact in company_compact or company_compact in name_compact)


def _application_host_ok(url: str | None, hosts: tuple[str, ...]) -> bool:
    if not url:
        return False
    try:
        netloc = urlsplit(url).netloc.lower()
    except ValueError:
        return False
    return any(netloc == host or netloc.endswith("." + host) for host in hosts)


def verify_sample(*, employer_name: str, adapter: str, jobs: list[RawJob]) -> dict[str, bool]:
    """The five checks SUP-02 requires. An empty sample fails every check --
    never vacuously passes just because nothing contradicted it."""
    hosts = _APPLICATION_HOSTS.get(adapter, ())
    has_jobs = bool(jobs)

    ids = [job.source_id for job in jobs if job.source_id]
    checks = {
        "sample_nonempty": has_jobs,
        "employer_identity_match": has_jobs and any(_identity_match(employer_name, job.company) for job in jobs),
        "unique_nonempty_ids": has_jobs and len(ids) == len(jobs) and len(set(ids)) == len(ids),
        "direct_application_urls": has_jobs and all(_application_host_ok(job.application_url, hosts) for job in jobs),
        "has_location": has_jobs and any((job.location or "").strip() for job in jobs),
        "has_full_description": has_jobs
        and any(len((job.description or "").strip()) >= _FULL_DESCRIPTION_MIN_CHARS for job in jobs),
    }
    checks["passed"] = all(checks.values())
    return checks


# ---------------------------------------------------------------------------
# Per-employer orchestration
# ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    name: str
    careers_url: str
    resolved_url: str | None
    evidence_date: str
    targets: list[str]
    status: str  # fetch_failed | adapter_not_detected | workday_site_unresolved | probed
    detected_candidates: list[dict[str, Any]]
    adapter: str | None
    endpoint_config: Any
    detection_method: str | None  # "static" | "js_render" | None (nothing usable found)
    sample_size: int
    checks: dict[str, bool]
    verified: bool
    error: str | None
    render_used: bool = False
    render_error: str | None = None


def probe_employer(
    client: httpx.Client,
    employer: dict,
    *,
    sample_size: int,
    fetch_timeout: int,
    renderer: "BrowserRenderer | None" = None,
) -> ProbeResult:
    name = employer["name"]
    careers_url = employer["careers_url"]
    targets = list(employer.get("targets") or [])
    evidence_date = date.today().isoformat()

    try:
        resp = client.get(careers_url, timeout=fetch_timeout)
        resolved_url = str(resp.url)
        html = resp.text if resp.status_code < 400 else ""
    except httpx.HTTPError as exc:
        return ProbeResult(
            name=name,
            careers_url=careers_url,
            resolved_url=None,
            evidence_date=evidence_date,
            targets=targets,
            status="fetch_failed",
            detected_candidates=[],
            adapter=None,
            endpoint_config=None,
            detection_method=None,
            sample_size=0,
            checks={},
            verified=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    candidates = detect_adapter_candidates(resolved_url, html, detection_method="static")
    usable = [c for c in candidates if c.endpoint_config is not None]

    # Many modern careers pages inject their ATS widget with client-side JS
    # (React/Next/Webflow) -- a plain httpx GET only ever sees the empty
    # shell. Only fall back to a headless render when the static scan found
    # nothing usable, and only when the caller actually supplied a renderer
    # (tests / --no-js-render runs pass None and skip this entirely).
    render_used = False
    render_error: str | None = None
    if not usable and renderer is not None:
        render = renderer.render(resolved_url)
        if render.error is not None:
            render_error = render.error
        elif render.html or render.network_urls:
            render_used = True
            js_url = render.final_url or resolved_url
            # Combine the rendered DOM with every network request URL the
            # page made -- a React/Next page that fetches its ATS's JSON
            # API directly via client-side fetch() never puts the slug in
            # page.content() at all, only in one of these request URLs.
            combined_text = render.html + "\n" + "\n".join(render.network_urls)
            js_candidates = detect_adapter_candidates(js_url, combined_text, detection_method="js_render")
            candidates = candidates + js_candidates
            usable = [c for c in js_candidates if c.endpoint_config is not None]
            if js_url != resolved_url:
                resolved_url = js_url

    detected_dump = [
        {
            "adapter": c.adapter,
            "endpoint_config": c.endpoint_config,
            "occurrences": c.occurrences,
            "detection_method": c.detection_method,
        }
        for c in candidates
    ]

    if not usable:
        status = "workday_site_unresolved" if any(c.adapter == "workday" for c in candidates) else "adapter_not_detected"
        return ProbeResult(
            name=name,
            careers_url=careers_url,
            resolved_url=resolved_url,
            evidence_date=evidence_date,
            targets=targets,
            status=status,
            detected_candidates=detected_dump,
            adapter=None,
            endpoint_config=None,
            detection_method=None,
            sample_size=0,
            checks={},
            verified=False,
            error=None,
            render_used=render_used,
            render_error=render_error,
        )

    best = usable[0]
    sample, fetch_error = fetch_sample(
        best.adapter, best.endpoint_config, timeout=fetch_timeout, sample_size=sample_size
    )
    checks = verify_sample(employer_name=name, adapter=best.adapter, jobs=sample)
    verified = bool(checks.get("passed")) and fetch_error is None

    return ProbeResult(
        name=name,
        careers_url=careers_url,
        resolved_url=resolved_url,
        evidence_date=evidence_date,
        targets=targets,
        status="probed",
        detected_candidates=detected_dump,
        adapter=best.adapter,
        endpoint_config=best.endpoint_config,
        detection_method=best.detection_method,
        sample_size=len(sample),
        checks=checks,
        verified=verified,
        error=fetch_error,
        render_used=render_used,
        render_error=render_error,
    )


# ---------------------------------------------------------------------------
# Batch run + artifact writers
# ---------------------------------------------------------------------------


def _load_cohort_employers(path: Path) -> list[dict]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(payload["employers"])


def _companies_patch_entry(adapter: str, endpoint_config: Any) -> tuple[str, Any]:
    if adapter == "workday":
        return "workday", dict(endpoint_config)
    return adapter, endpoint_config


def run_probe(
    *,
    cohort_path: Path,
    out_dir: Path,
    only: set[str] | None,
    sample_size: int,
    fetch_timeout: int,
    use_js_render: bool = True,
    render_timeout_ms: int = 20000,
) -> dict:
    employers = _load_cohort_employers(cohort_path)
    if only:
        employers = [e for e in employers if e["name"] in only]

    # The renderer is always constructed (cheap -- no browser launch happens
    # until .render() is actually called) so cleanup always runs, but it's
    # only ever passed to probe_employer when the fallback is enabled.
    renderer = BrowserRenderer(timeout_ms=render_timeout_ms)
    active_renderer = renderer if use_js_render else None

    results: list[ProbeResult] = []
    total = len(employers)
    with renderer, httpx.Client(headers=DEFAULT_HEADERS, timeout=fetch_timeout, follow_redirects=True) as client:
        for i, employer in enumerate(employers, start=1):
            name = employer.get("name", "?")
            # Printed as each employer starts, not just at the end -- a
            # JS-render fallback run over 20-30 employers can legitimately
            # take several minutes, and a silent script that long looks
            # identical to a hung one.
            print(f"[{i}/{total}] probing {name}...", file=sys.stderr, flush=True)
            result = probe_employer(
                client,
                employer,
                sample_size=sample_size,
                fetch_timeout=fetch_timeout,
                renderer=active_renderer,
            )
            results.append(result)
            outcome = "verified" if result.verified else result.status
            suffix = " [js render]" if result.render_used else ""
            print(f"    -> {outcome}{suffix}", file=sys.stderr, flush=True)

    verified = [r for r in results if r.verified]
    failed = [r for r in results if not r.verified]
    render_used_count = sum(1 for r in results if r.render_used)

    out_dir.mkdir(parents=True, exist_ok=True)

    audit = {
        "run_at": datetime.now(UTC).isoformat(),
        "cohort_path": str(cohort_path),
        "total_employers": len(results),
        "verified_count": len(verified),
        "failed_count": len(failed),
        "js_render_fallback_used_count": render_used_count,
        "results": [asdict(r) for r in results],
    }
    audit_path = out_dir / "employer_cohort_probe.json"
    audit_path.write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")

    # Proposed companies.yaml patch: verified endpoints only. Never written
    # to config/companies.yaml itself -- a human merges this by hand.
    patch: dict[str, list] = {}
    for r in verified:
        key, value = _companies_patch_entry(r.adapter, r.endpoint_config)
        patch.setdefault(key, [])
        if value not in patch[key]:
            patch[key].append(value)
    patch_path = out_dir / "employer_cohort_probe.proposed_companies_patch.yaml"
    patch_path.write_text(
        "# SUP-02 proposed patch -- generated by scripts/probe_employer_cohort.py.\n"
        "# NOT applied to config/companies.yaml. Review each entry against\n"
        "# employer_cohort_probe.json's evidence before merging by hand.\n"
        + yaml.safe_dump(patch, sort_keys=True, default_flow_style=False),
        encoding="utf-8",
    )

    # Proposed cohort verification_status patch. enabled is always false --
    # scripts/validate_employer_cohort.py requires verification_status
    # "verified_approved" (a distinct, human-only status) before enabled may
    # ever be true, so a passing probe proposes "verified_probe_passed" and
    # stops there.
    cohort_patch = {
        r.name: {
            "verification_status": "verified_probe_passed" if r.verified else "probe_failed",
            "evidence_date": r.evidence_date,
            "adapter": r.adapter,
            "endpoint_config": r.endpoint_config,
            "enabled": False,
        }
        for r in results
    }
    cohort_patch_path = out_dir / "employer_cohort_probe.proposed_cohort_status.yaml"
    cohort_patch_path.write_text(
        "# SUP-02 proposed verification_status updates -- generated, NOT applied\n"
        "# to config/employer_cohort.v1.yaml. enabled stays false for every\n"
        "# entry; only an explicit human review sets verification_status:\n"
        "# verified_approved and enabled: true (see scripts/validate_employer_cohort.py).\n"
        + yaml.safe_dump(cohort_patch, sort_keys=True, default_flow_style=False),
        encoding="utf-8",
    )

    return {
        "audit_path": str(audit_path),
        "companies_patch_path": str(patch_path),
        "cohort_status_patch_path": str(cohort_patch_path),
        "total_employers": len(results),
        "verified_count": len(verified),
        "failed_count": len(failed),
        "js_render_fallback_used_count": render_used_count,
        "verified_employers": [r.name for r in verified],
        "failed_employers": [
            {
                "name": r.name,
                "status": r.status,
                "error": r.error,
                "render_used": r.render_used,
                "render_error": r.render_error,
            }
            for r in failed
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cohort", type=Path, default=PROJECT_ROOT / "config" / "employer_cohort.v1.yaml")
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / "data" / "audits")
    parser.add_argument("--only", type=str, default=None, help="Comma-separated employer names to probe (default: all)")
    parser.add_argument("--sample-size", type=int, default=8)
    parser.add_argument("--fetch-timeout", type=int, default=20)
    parser.add_argument(
        "--no-js-render",
        action="store_true",
        help="Disable the headless-browser fallback for employers where static HTML detection finds nothing "
        "(faster, but misses JS-rendered careers pages; requires 'playwright install chromium' when enabled)",
    )
    parser.add_argument("--render-timeout-ms", type=int, default=20000)
    args = parser.parse_args()

    only = {name.strip() for name in args.only.split(",")} if args.only else None
    summary = run_probe(
        cohort_path=args.cohort,
        out_dir=args.out_dir,
        only=only,
        sample_size=args.sample_size,
        fetch_timeout=args.fetch_timeout,
        use_js_render=not args.no_js_render,
        render_timeout_ms=args.render_timeout_ms,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
