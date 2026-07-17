"""Phase S4 / SUP-07 CLI: recover full JDs for snippet-only Adzuna postings.

Run manually (this is a first pass, not yet wired into scheduling -- see
Phase S6 in ``docs/JOB_SUPPLY_EXPANSION_PLAN.md`` for when/if that happens):

    uv run python scripts/resolve_adzuna_snippets.py
    uv run python scripts/resolve_adzuna_snippets.py --limit 200 --max-attempts 5

Needs live Postgres (reads/writes the ``jobs`` table and, where a Job Index
posting already exists for a job, writes a new immutable JobSnapshot too --
see ``src/application/resolve_snippets.py`` for the full contract). Never
enables a disabled adapter, never fabricates a snapshot on a failed
recovery attempt, and never touches ``config/companies.yaml`` or
``config/source_policy.yaml``.
"""

from __future__ import annotations

import argparse
import json
import sys

from src.application.resolve_snippets import (
    DEFAULT_BATCH_LIMIT,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MIN_REVIEW_INDEX,
    load_source_policy,
    resolve_pending_snippets,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DEFAULT_BATCH_LIMIT)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument(
        "--minimum-review-index",
        type=float,
        default=DEFAULT_MIN_REVIEW_INDEX,
        help="Only recover jobs meeting this existing V2 review-index floor.",
    )
    args = parser.parse_args()

    from src.core.config import load_config  # noqa: PLC0415
    from src.core.database import get_session_factory  # noqa: PLC0415

    session_factory = get_session_factory(load_config())
    policy = load_source_policy()

    with session_factory() as session, session.begin():
        summary = resolve_pending_snippets(
            session,
            max_attempts=args.max_attempts,
            limit=args.limit,
            minimum_review_index=args.minimum_review_index,
            source_policy=policy,
        )

    print(
        json.dumps(
            {
                "considered": summary.considered,
                "recovered": summary.recovered,
                "failed": summary.failed,
                "failure_reasons": summary.failure_reasons,
            },
            indent=2,
        ),
        file=sys.stdout,
    )


if __name__ == "__main__":
    main()
