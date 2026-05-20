from __future__ import annotations

from pathlib import Path


def test_direct_yaml_readers_bootstrap_example_configs(
    tmp_path: Path, monkeypatch
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "companies.yaml.example").write_text(
        "greenhouse:\n  - acme\n", encoding="utf-8"
    )
    (config_dir / "filters.yaml.example").write_text(
        "profiles:\n"
        "  default:\n"
        "    locations:\n"
        "      - Canada\n"
        "    title_include:\n"
        "      - engineer\n",
        encoding="utf-8",
    )
    (config_dir / "search_profiles.yaml.example").write_text(
        "profiles:\n  saved-search:\n    keyword: software engineer\n    location: Canada\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("src.core.config.PROJECT_ROOT", tmp_path)

    from src.application import search_profiles
    from src.intake.batch import load_company_list
    from src.intake.filters import load_filter_profiles

    monkeypatch.setattr(
        search_profiles,
        "SEARCH_PROFILES_PATH",
        config_dir / "search_profiles.yaml",
    )

    companies = load_company_list(config_dir / "companies.yaml")
    filters = load_filter_profiles(config_dir / "filters.yaml")
    saved_searches = search_profiles.load_search_profiles_data()

    assert companies == {"greenhouse": ["acme"]}
    assert "default" in filters
    assert [p["id"] for p in saved_searches["profiles"]] == ["saved-search"]
