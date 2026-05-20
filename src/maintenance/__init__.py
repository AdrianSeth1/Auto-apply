"""Phase 18.4: maintenance utilities for AutoApply.

Two sibling concerns live here:

* :mod:`src.maintenance.atomic` -- a small ``atomic_write`` context
  manager so generators never leave half-written DOCX / PDF / JSON on
  disk when a crash interrupts a write. Half-written files were the
  ``patched_resume_<uuid>.docx`` ghost class that drove Phase 18.4's
  scope in the first place.

* :mod:`src.maintenance.artifacts` -- the reference-aware artifact
  cleanup pipeline that protects DB-referenced user assets and moves
  orphan / tmp / failed outputs into ``data/quarantine/<run_id>/``
  with a configurable purge window. The ``maintenance.cache_eviction``
  Beat task is the production driver; ``autoapply cleanup ...``
  shares the same code path so manual operations can never out-vote
  the scheduler.
"""

from src.maintenance.atomic import AtomicWriteError, atomic_write

__all__ = ["AtomicWriteError", "atomic_write"]
