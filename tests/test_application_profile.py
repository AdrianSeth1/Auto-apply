"""Regression tests for src.application.profile.

The legacy-migration branch in ``_ensure_profile_store`` used to fire on
every call as long as ``data/profile/profile.yaml`` (the legacy
single-file layout) was on disk and ``profiles/default.yaml`` was
missing. That made it impossible to rename or delete the ``default``
profile: the file would simply be re-copied from the legacy path on the
next ``load_profile_data`` call. These tests pin that behavior to
"one-shot, only when profiles/ is empty, and then unlink the legacy
file."
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.application import profile as profile_module


@pytest.fixture()
def profile_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the module's path constants to a fresh tmp dir."""
    profile_dir = tmp_path / "profile"
    profiles_dir = profile_dir / "profiles"
    legacy_file = profile_dir / "profile.yaml"
    active_file = profile_dir / "active_profile.txt"

    monkeypatch.setattr(profile_module, "PROFILE_DIR", profile_dir)
    monkeypatch.setattr(profile_module, "PROFILES_DIR", profiles_dir)
    monkeypatch.setattr(profile_module, "LEGACY_PROFILE_FILE", legacy_file)
    monkeypatch.setattr(profile_module, "ACTIVE_PROFILE_FILE", active_file)
    return profile_dir


def _write_legacy(profile_dir: Path, body: str = "identity:\n  full_name: Legacy\n") -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "profile.yaml").write_text(body, encoding="utf-8")


def test_legacy_migration_runs_once_and_removes_legacy_file(profile_store: Path) -> None:
    _write_legacy(profile_store)

    profile_module._ensure_profile_store()

    assert (profile_store / "profiles" / "default.yaml").exists()
    # Legacy file should be gone after a successful one-shot migration.
    assert not (profile_store / "profile.yaml").exists()


def test_delete_default_profile_does_not_resurrect_from_legacy(profile_store: Path) -> None:
    """The original bug: deleting ``default`` left ``profile.yaml`` on
    disk, and the next ``load_profile_data`` call re-copied it as
    ``profiles/default.yaml``, making the profile undeletable."""
    _write_legacy(profile_store)
    profile_module._ensure_profile_store()  # migrate
    assert (profile_store / "profiles" / "default.yaml").exists()

    result = profile_module.delete_profile_data(profile_id="default")
    assert result["ok"] is True

    # Trigger another store-ensure cycle (this is what re-resurrected
    # the profile before the fix).
    profile_module._ensure_profile_store()
    assert not (profile_store / "profiles" / "default.yaml").exists()
    ids = {p["id"] for p in profile_module.list_profiles()}
    assert "default" not in ids


def test_rename_default_profile_does_not_leave_phantom_default(profile_store: Path) -> None:
    """The reported bug: renaming the default profile created a new
    file with the new name but the original ``default`` came back."""
    _write_legacy(profile_store)
    profile_module._ensure_profile_store()

    result = profile_module.rename_profile_data(
        profile_id="default", new_profile_id="my-resume"
    )
    assert result["ok"] is True

    ids = {p["id"] for p in profile_module.list_profiles()}
    assert ids == {"my-resume"}, f"Phantom default profile resurrected: {ids}"
