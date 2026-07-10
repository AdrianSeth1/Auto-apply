"""Self-growing ATS board registry.

2026-07-08: there is no global "search all of Greenhouse/Lever" API —
both vendors expose per-company boards only, so `companies.yaml` is the
entire ATS search universe. But the LinkedIn scraper already resolves
postings to their external ATS URLs, which means every LinkedIn search
DISCOVERS boards we don't know about yet. This module harvests those
slugs and appends them to companies.yaml, so the board list grows from
the user's actual search results instead of hand curation.

Design constraints:
  * Best-effort: discovery failures must never break a search.
  * companies.yaml carries human comments — pyyaml round-trips would
    destroy them, so new slugs are inserted TEXTUALLY right under the
    ``greenhouse:`` / ``lever:`` key lines.
  * A bad slug is cheap: the next board fetch logs one error and moves on.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

import yaml

logger = logging.getLogger("autoapply.intake.board_discovery")

# boards.greenhouse.io/{slug}/jobs/123, job-boards.greenhouse.io/{slug}/...,
# boards.greenhouse.io/embed/job_app?for={slug}&token=...
_GREENHOUSE_RE = re.compile(
    r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9][a-z0-9_-]*)",
    re.IGNORECASE,
)
_LEVER_RE = re.compile(
    r"jobs\.lever\.co/([A-Za-z0-9][A-Za-z0-9_-]*)", re.IGNORECASE
)
# jobs.ashbyhq.com/{slug}, jobs.ashbyhq.com/{slug}/{job_id}, .../application
_ASHBY_RE = re.compile(
    r"jobs\.ashbyhq\.com/([A-Za-z0-9][A-Za-z0-9_-]*)", re.IGNORECASE
)
# URL path segments that can follow the domain but are not slugs.
_NOT_SLUGS = {"embed", "job_app", "jobs"}

_write_lock = threading.Lock()


def discover_board_slugs(jobs) -> dict[str, set[str]]:
    """Extract greenhouse/lever/ashby slugs from jobs' apply/redirect URLs."""
    found: dict[str, set[str]] = {"greenhouse": set(), "lever": set(), "ashby": set()}
    for job in jobs:
        urls = [getattr(job, "application_url", None)]
        raw = getattr(job, "raw_data", None) or {}
        urls.extend(
            raw.get(key)
            for key in ("manual_apply_url", "external_url", "redirect_url", "ats_url")
        )
        for url in urls:
            if not url:
                continue
            match = _GREENHOUSE_RE.search(url)
            if match:
                slug = match.group(1).lower()
                if slug not in _NOT_SLUGS:
                    found["greenhouse"].add(slug)
            match = _LEVER_RE.search(url)
            if match:
                slug = match.group(1).lower()
                if slug not in _NOT_SLUGS:
                    found["lever"].add(slug)
            match = _ASHBY_RE.search(url)
            if match:
                slug = match.group(1).lower()
                if slug not in _NOT_SLUGS:
                    found["ashby"].add(slug)
    return found


def register_discovered_boards(jobs, config_dir: Path) -> int:
    """Append newly seen board slugs to companies.yaml. Returns count added."""
    try:
        path = Path(config_dir) / "companies.yaml"
        if not path.exists():
            return 0
        discovered = discover_board_slugs(jobs)
        if not (discovered["greenhouse"] or discovered["lever"] or discovered["ashby"]):
            return 0

        with _write_lock:
            text = path.read_text(encoding="utf-8")
            known = yaml.safe_load(text) or {}
            added = 0
            for ats in ("greenhouse", "lever", "ashby"):
                existing = {
                    str(slug).lower() for slug in (known.get(ats) or [])
                }
                new_slugs = sorted(discovered[ats] - existing)
                if not new_slugs:
                    continue
                lines = "".join(
                    f"  - {slug}  # auto-discovered from LinkedIn results\n"
                    for slug in new_slugs
                )
                key_pattern = re.compile(rf"^{ats}:\s*$", re.MULTILINE)
                match = key_pattern.search(text)
                if match:
                    insert_at = match.end() + 1  # just past the key line's newline
                    text = text[:insert_at] + lines + text[insert_at:]
                else:
                    text = text.rstrip() + f"\n\n{ats}:\n{lines}"
                added += len(new_slugs)
            if added:
                path.write_text(text, encoding="utf-8")
                logger.info(
                    "Board discovery: added %d new ATS board(s) to %s", added, path
                )
        return added
    except Exception:  # noqa: BLE001 -- discovery must never break a search
        logger.warning("Board discovery failed; continuing.", exc_info=True)
        return 0
