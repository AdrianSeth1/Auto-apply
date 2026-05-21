"""Phase 18.4: tests for the cleanup classifier.

The pure-function :func:`classify_path` decides whether a file under
``data/output`` is protected, a half-written tmp, a screenshot, an
orphan, or unknown. These tests cover every category branch without
needing a database or a real ``data/output`` tree.
"""

from __future__ import annotations

from pathlib import Path

from src.maintenance.artifacts import (
    CATEGORY_FAILED_ARTIFACT,
    CATEGORY_ORPHAN_OUTPUT,
    CATEGORY_PROTECTED,
    CATEGORY_SCREENSHOT,
    CATEGORY_TMP,
    CATEGORY_VERSION_LOG,
    classify_path,
    load_cleanup_config,
)


def test_protected_path_short_circuits_to_protected(tmp_path: Path) -> None:
    target = tmp_path / "report.docx"
    target.write_bytes(b"PK\x03\x04")
    result = classify_path(target, protected={target.resolve()})
    assert result.category == CATEGORY_PROTECTED
    assert result.eligible is False


def test_tmp_suffix_is_eligible_tmp(tmp_path: Path) -> None:
    target = tmp_path / "patched_resume_abc.docx.aa11bb.tmp"
    target.write_bytes(b"")
    result = classify_path(target, protected=set())
    assert result.category == CATEGORY_TMP
    assert result.eligible is True


def test_part_suffix_is_eligible_tmp(tmp_path: Path) -> None:
    target = tmp_path / "legacy.part"
    target.write_bytes(b"")
    result = classify_path(target, protected=set())
    assert result.category == CATEGORY_TMP
    assert result.eligible is True


def test_screenshot_dir_is_screenshot(tmp_path: Path) -> None:
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    target = screenshots / "abc_form_opened.png"
    target.write_bytes(b"PNG")
    result = classify_path(target, protected=set())
    assert result.category == CATEGORY_SCREENSHOT


def test_versions_dir_is_version_log(tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    versions.mkdir()
    target = versions / "20260520_resume_pdf_v1.json"
    target.write_text("{}", encoding="utf-8")
    result = classify_path(target, protected=set())
    assert result.category == CATEGORY_VERSION_LOG


def test_zero_byte_docx_is_failed_artifact(tmp_path: Path) -> None:
    target = tmp_path / "patched_resume_oops.docx"
    target.write_bytes(b"")
    result = classify_path(target, protected=set())
    assert result.category == CATEGORY_FAILED_ARTIFACT
    assert result.eligible is True


def test_real_docx_is_orphan_when_not_protected(tmp_path: Path) -> None:
    target = tmp_path / "resume_foo_2026.docx"
    target.write_bytes(b"PK\x03\x04something")
    result = classify_path(target, protected=set())
    assert result.category == CATEGORY_ORPHAN_OUTPUT
    assert result.eligible is True


def test_load_cleanup_config_uses_defaults_for_missing_keys() -> None:
    cfg = load_cleanup_config(config={"cleanup": {"tmp_hours": 12}})
    assert cfg.tmp_hours == 12
    # Other defaults survive.
    assert cfg.quarantine_days == 7
    assert cfg.screenshot_keep_per_application == 5


def test_load_cleanup_config_rejects_negative_values() -> None:
    cfg = load_cleanup_config(config={"cleanup": {"tmp_hours": -3}})
    assert cfg.tmp_hours == 24  # default
