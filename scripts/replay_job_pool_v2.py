"""Replay a frozen JSON dataset without network calls or operational writes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.evaluation.job_pool import canonical_json, replay_frozen_items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="JSON array or {items: [...]} frozen dataset")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--capacity", type=int, default=20)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = replay_frozen_items(payload.get("items", payload), capacity=args.capacity)
    rendered = canonical_json(result) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()

