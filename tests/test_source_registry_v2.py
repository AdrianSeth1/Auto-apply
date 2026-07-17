from __future__ import annotations

from pathlib import Path

from src.intake.source_registry import load_endpoint_seeds


def test_companies_yaml_is_a_read_only_idempotent_seed_source(tmp_path: Path) -> None:
    path = tmp_path / "companies.yaml"
    content = "greenhouse:\n  - Acme\nworkday:\n  - tenant: Demo\n    host: wd5\n    site: Careers\n"
    path.write_text(content, encoding="utf-8")
    before = path.read_bytes()
    first = load_endpoint_seeds(path)
    second = load_endpoint_seeds(path)
    assert first == second
    assert path.read_bytes() == before
    assert [(seed.adapter, seed.endpoint_key) for seed in first] == [
        ("greenhouse", "acme"),
        ("workday", "demo|wd5|careers"),
    ]


def test_known_repeated_404_endpoints_seed_degraded_not_deleted(tmp_path: Path) -> None:
    path = tmp_path / "companies.yaml"
    path.write_text(
        "greenhouse:\n  - sentry\n  - gong\nashby:\n  - p-1\n",
        encoding="utf-8",
    )
    seeds = load_endpoint_seeds(path)
    assert len(seeds) == 3
    assert {seed.state for seed in seeds} == {"degraded"}
