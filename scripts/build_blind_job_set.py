"""Create a seeded, identity-hidden prospective review set from replay JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.evaluation.job_pool import build_blind_set, canonical_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("replay", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", default="job-pool-v2-blind-1")
    parser.add_argument("--minimum", type=int, default=50)
    args = parser.parse_args()
    replay = json.loads(args.replay.read_text(encoding="utf-8"))
    result = build_blind_set(replay, seed=args.seed, minimum=args.minimum)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(result) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

