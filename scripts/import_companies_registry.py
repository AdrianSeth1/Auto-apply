"""Dry-run or import companies.yaml into the V2 endpoint registry."""

from __future__ import annotations

import argparse

from src.core.config import load_config
from src.core.database import get_session_factory
from src.intake.source_registry import import_endpoint_seeds


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    factory = get_session_factory(load_config())
    with factory() as session:
        counts = import_endpoint_seeds(session, dry_run=not args.write)
        if args.write:
            session.commit()
    print({**counts, "mode": "write" if args.write else "dry_run"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
