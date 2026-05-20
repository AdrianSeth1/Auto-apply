"""Phase 18.4: tests for :mod:`src.maintenance.atomic`.

The atomic-write helper is the gate between a generator's
in-progress bytes and the durable artifact path. These tests cover
the four contracts that matter to cleanup:

* Successful writes show up at the target, with no ``.tmp`` sibling
  left behind.
* Exceptions roll back: the tmp sibling is unlinked and the original
  target is untouched.
* Writers that exit without producing the tmp file raise
  :class:`AtomicWriteError` instead of silently creating an empty
  target.
* The tmp suffix matches what the cleanup classifier looks for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.maintenance.atomic import TMP_SUFFIX, AtomicWriteError, atomic_write


def test_atomic_write_happy_path(tmp_path: Path) -> None:
    target = tmp_path / "subdir" / "report.json"
    with atomic_write(target) as tmp:
        tmp.write_text("hello", encoding="utf-8")

    assert target.read_text(encoding="utf-8") == "hello"
    # No leftover ``.tmp`` sibling.
    leftover = list(tmp_path.rglob(f"*{TMP_SUFFIX}"))
    assert leftover == [], f"unexpected tmp leftovers: {leftover}"


def test_atomic_write_overwrites_existing_target(tmp_path: Path) -> None:
    target = tmp_path / "report.txt"
    target.write_text("stale", encoding="utf-8")
    with atomic_write(target) as tmp:
        tmp.write_text("fresh", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "fresh"


def test_atomic_write_rolls_back_on_exception(tmp_path: Path) -> None:
    target = tmp_path / "doomed.docx"
    target.write_text("keep me", encoding="utf-8")

    with pytest.raises(RuntimeError):
        with atomic_write(target) as tmp:
            tmp.write_text("half written", encoding="utf-8")
            raise RuntimeError("simulated crash")

    # Original target unchanged, tmp unlinked.
    assert target.read_text(encoding="utf-8") == "keep me"
    leftover = list(tmp_path.rglob(f"*{TMP_SUFFIX}"))
    assert leftover == []


def test_atomic_write_no_tmp_written_raises(tmp_path: Path) -> None:
    target = tmp_path / "nothing.docx"
    with pytest.raises(AtomicWriteError):
        with atomic_write(target):
            pass
    assert not target.exists()


def test_atomic_write_tmp_suffix_is_recognised_by_classifier(tmp_path: Path) -> None:
    """Cleanup classifier identifies leftovers by the ``.tmp`` suffix.
    We pin the suffix here so a typo in the helper can't drift the two
    sides apart silently."""
    assert TMP_SUFFIX == ".tmp"
