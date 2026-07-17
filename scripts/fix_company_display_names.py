"""One-off data repair: correct slug-derived company display names.

2026-07-16. The Greenhouse adapter's ``_infer_company_name`` looked for a
nested ``company.name`` field that the board API doesn't send; the flat
``company_name`` it does send ("First Due", "Domino Data Lab", "Yes
Energy", …) was ignored, so every Greenhouse job stored a title-cased
board slug ("Localitymediallcdbafirstdue") as its company — which then
appeared verbatim in generated cover letters. The adapter is fixed; this
script repairs the rows that were ingested before the fix.

What it touches (display-name columns only — never snapshots/content):
  * legacy ``jobs.company``            (from jobs.raw_data.company_name)
  * ``job_postings.company``           (from the latest snapshot raw_data)
  * pending ``review_queue.company``   (denormalized display copy)

What it never touches: job_snapshots content, evaluations, decisions,
submitted/approved review entries (those are historical evidence — the
audit below reports them instead so the user knows what went out).

Usage:
    uv run python scripts/fix_company_display_names.py            # dry run
    uv run python scripts/fix_company_display_names.py --apply    # write
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from sqlalchemy import select

from src.core.database import get_session_factory
from src.core.models import Job, JobPosting, JobSnapshot, ReviewQueueEntry


def _sanitize(value: object) -> str | None:
    """Match the adapter's sanitizer: drop zero-width/format chars."""
    if not isinstance(value, str):
        return None
    cleaned = "".join(ch for ch in value if ch.isprintable() and ch not in "‎‏​﻿")
    cleaned = " ".join(cleaned.split())
    return cleaned or None


def _extract_real_name(raw_data: dict | None) -> str | None:
    if not isinstance(raw_data, dict):
        return None
    name = _sanitize(raw_data.get("company_name"))
    if name:
        return name
    nested = raw_data.get("company")
    if isinstance(nested, dict):
        return _sanitize(nested.get("name"))
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    args = parser.parse_args()

    factory = get_session_factory()
    fixed = Counter()
    renames: dict[tuple[str, str], tuple[str, str]] = {}  # (source, source_id) -> (old, new)

    with factory() as session, session.begin():
        # --- legacy jobs table (raw_data lives on the row) -----------
        rows = session.execute(
            select(Job).where(Job.source == "greenhouse")
        ).scalars().all()
        for row in rows:
            real = _extract_real_name(row.raw_data)
            if real and real != row.company:
                renames[(row.source or "", row.source_id or "")] = (row.company, real)
                fixed["jobs"] += 1
                if args.apply:
                    row.company = real

        # --- job_postings (raw payload on latest snapshot) -----------
        postings = session.execute(
            select(JobPosting).where(JobPosting.source == "greenhouse")
        ).scalars().all()
        for posting in postings:
            snapshot = session.execute(
                select(JobSnapshot)
                .where(JobSnapshot.posting_id == posting.id)
                .order_by(JobSnapshot.scraped_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            real = _extract_real_name(snapshot.raw_data if snapshot else None)
            if real and real != posting.company:
                renames[(posting.source, posting.source_id)] = (posting.company, real)
                fixed["job_postings"] += 1
                if args.apply:
                    posting.company = real

        # --- denormalized review_queue display names ------------------
        # Only PENDING cards: approved/submitted entries are historical
        # evidence of what was actually sent and must not be rewritten.
        old_names = {old for old, _new in renames.values()}
        pending = session.execute(
            select(ReviewQueueEntry).where(ReviewQueueEntry.status == "pending")
        ).scalars().all()
        for entry in pending:
            if entry.company in old_names:
                new_name = next(new for old, new in renames.values() if old == entry.company)
                fixed["review_queue_pending"] += 1
                if args.apply:
                    entry.company = new_name

        # --- audit: what already went out with a slug name ------------
        sent = session.execute(
            select(ReviewQueueEntry).where(
                ReviewQueueEntry.status.in_(("approved", "submitted"))
            )
        ).scalars().all()
        affected_sent = [
            (entry.status, entry.company, entry.title, entry.submitted_at)
            for entry in sent
            if entry.company in old_names
        ]

    mode = "APPLIED" if args.apply else "DRY RUN (pass --apply to write)"
    print(f"== company display-name repair — {mode} ==")
    for table, count in sorted(fixed.items()):
        print(f"  {table}: {count} row(s) corrected")
    if not fixed:
        print("  nothing to fix")
    print(f"\n== distinct renames ({len(set(renames.values()))}) ==")
    for old, new in sorted(set(renames.values())):
        print(f"  {old!r} -> {new!r}")
    print(f"\n== ALREADY SENT/approved with a slug company name ({len(affected_sent)}) ==")
    for status, company, title, submitted_at in affected_sent:
        print(f"  [{status}] {company} — {title} (submitted_at={submitted_at})")
    if affected_sent:
        print(
            "\nThese applications went out with the wrong company display "
            "name in their materials. Historical rows were NOT rewritten."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
