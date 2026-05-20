"""Phase 18.4: atomic-write helper.

Every materials generator (``generate_resume``, ``generate_cover_letter``,
``patch_resume_docx``, ``_copy_library_document_to_output``,
``save_generation_version``) used to write directly to its final path.
A crash mid-write left half-written ``patched_resume_<uuid>.docx``
files on disk; the cleanup classifier needed a way to tell those
half-written outputs apart from real artifacts, so we gave generators
a context manager that writes to a ``.tmp`` sibling and renames on
success.

Contract::

    with atomic_write(target) as tmp_path:
        tmp_path.write_bytes(payload)
        # ...or hand tmp_path to a DOCX library that writes there directly.
    # On normal exit the tmp_path is renamed to target.
    # On exception the tmp_path is unlinked and the original target is
    # left untouched.

``target`` may already exist; the rename overwrites atomically on
POSIX. On Windows, ``os.replace`` is used which is also atomic for
same-volume renames. The tmp suffix is hard-coded to ``.tmp`` so the
:mod:`src.maintenance.artifacts` classifier can recognise leftover
sibling files from older releases and quarantine them.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)


#: Suffix appended to the target path while a write is in flight.
#: ``data/output/foo.docx`` → ``data/output/foo.docx.<uuid>.tmp``. Both
#: the cleanup classifier (Phase 18.4) and any human looking in
#: ``data/output`` should be able to spot these immediately.
TMP_SUFFIX = ".tmp"


class AtomicWriteError(Exception):
    """Raised when the atomic rename itself fails. Callers that get
    this can assume the original target is unchanged and the tmp file
    has been unlinked (best-effort)."""


@contextmanager
def atomic_write(target: str | os.PathLike[str]) -> Iterator[Path]:
    """Yield a tmp path that gets renamed onto ``target`` on success.

    Parameters
    ----------
    target:
        Final destination path. Parent directory must exist (or be
        creatable) before calling; this helper creates parents
        idempotently so call-sites don't have to.

    Behaviour
    ---------
    * The yielded path is ``target`` with a random suffix appended
      (e.g. ``foo.docx.4a..tmp``). Callers write to it.
    * If the ``with`` block exits cleanly, the tmp path is renamed
      onto ``target``. ``os.replace`` is used so an existing target
      is overwritten atomically on both POSIX and Windows.
    * If the ``with`` block raises, the tmp path is unlinked and the
      original target is left untouched. The original exception is
      re-raised.
    * If the rename itself fails (e.g. cross-device or permissions),
      :class:`AtomicWriteError` is raised after best-effort cleanup
      of the tmp file.

    The function does NOT fsync. Materials generation is many minutes
    of LLM work; the marginal durability win from fsyncing every DOCX
    write is not worth the latency cost. Cleanup is the safety net.
    """
    final_path = Path(target)
    final_path.parent.mkdir(parents=True, exist_ok=True)

    # Random tail so two concurrent writers to the same target don't
    # collide. Cleanup uses the ``.tmp`` suffix marker -- not the
    # random tail -- to recognise leftovers.
    tmp_path = final_path.with_name(f"{final_path.name}.{uuid4().hex[:12]}{TMP_SUFFIX}")

    try:
        yield tmp_path
    except BaseException:
        _best_effort_unlink(tmp_path)
        raise

    if not tmp_path.exists():
        # Writer block returned without producing the tmp file. This
        # is almost always a bug in the caller, but we tolerate it
        # rather than silently creating an empty target.
        raise AtomicWriteError(
            f"atomic_write block exited without writing to {tmp_path}"
        )

    try:
        os.replace(tmp_path, final_path)
    except OSError as exc:
        _best_effort_unlink(tmp_path)
        raise AtomicWriteError(
            f"failed to rename {tmp_path} -> {final_path}: {exc}"
        ) from exc


def _best_effort_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:  # noqa: PERF203 -- best-effort cleanup
        logger.warning("atomic_write: could not remove tmp file %s", path)


__all__ = ["AtomicWriteError", "TMP_SUFFIX", "atomic_write"]
