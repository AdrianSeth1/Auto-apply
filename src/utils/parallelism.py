"""Phase 18.5: process-wide rate-limit primitives for LLM calls.

The Phase 18 worker pool runs N Celery processes; each task body that
fans out via :func:`asyncio.gather` + :func:`asyncio.to_thread` can
multiply provider concurrency several times over per process. Without
a *shared* throttle a single misbehaving rewrite job has been observed
to burn provider quota for an entire org.

We expose two layers:

* :func:`global_llm_semaphore` -- the process-wide cap that every
  LLM dispatch must hold. Defaults to ``parallelism.llm.max_concurrent_global``.
* :func:`provider_semaphore(provider_id)` -- a per-provider cap on
  top of the global one. ``parallelism.provider.<id>.max_concurrent``
  overrides; missing entries fall back to the global cap.

Both are :class:`threading.Semaphore` rather than asyncio primitives
so the same instance works for sync subprocess calls
(``claude-cli`` / ``codex-cli``), threadpool calls, and
``asyncio.to_thread`` fan-out. The :func:`llm_call_gate` context
manager acquires both in the right order.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from src.core.config import load_config

logger = logging.getLogger(__name__)


_DEFAULT_GLOBAL_MAX_CONCURRENT = 10
_DEFAULT_BULLET_REWRITES_PER_TASK = 5
_DEFAULT_PROVIDER_MAX_CONCURRENT = _DEFAULT_GLOBAL_MAX_CONCURRENT


_LOCK = threading.Lock()
_GLOBAL_SEM: threading.Semaphore | None = None
_PROVIDER_SEMS: dict[str, threading.Semaphore] = {}
_GLOBAL_CAP: int = _DEFAULT_GLOBAL_MAX_CONCURRENT
_PROVIDER_CAPS: dict[str, int] = {}
_BULLET_CAP: int = _DEFAULT_BULLET_REWRITES_PER_TASK


def _load_caps(config: dict[str, Any] | None = None) -> None:
    """(Re)read the ``parallelism`` block from settings.

    Idempotent + cheap; callers tolerate a momentary mismatch between
    config-edit time and the next semaphore acquisition.
    """
    global _GLOBAL_CAP, _PROVIDER_CAPS, _BULLET_CAP, _GLOBAL_SEM, _PROVIDER_SEMS

    raw = (config or load_config()).get("parallelism") or {}
    if not isinstance(raw, dict):
        raw = {}

    llm_raw = raw.get("llm") or {}
    provider_raw = raw.get("provider") or {}
    rewrites_raw = raw.get("bullet_rewrites") or {}

    def _int(value: Any, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        if parsed < 1:
            return default
        return parsed

    new_global_cap = _int(
        llm_raw.get("max_concurrent_global"), _DEFAULT_GLOBAL_MAX_CONCURRENT
    )
    new_bullet_cap = _int(
        rewrites_raw.get("max_concurrent_per_task"),
        _DEFAULT_BULLET_REWRITES_PER_TASK,
    )
    new_provider_caps: dict[str, int] = {}
    if isinstance(provider_raw, dict):
        for pid, block in provider_raw.items():
            if not isinstance(block, dict):
                continue
            new_provider_caps[str(pid)] = _int(
                block.get("max_concurrent"), _DEFAULT_PROVIDER_MAX_CONCURRENT
            )

    with _LOCK:
        if new_global_cap != _GLOBAL_CAP or _GLOBAL_SEM is None:
            _GLOBAL_CAP = new_global_cap
            _GLOBAL_SEM = threading.Semaphore(new_global_cap)
        _BULLET_CAP = new_bullet_cap
        # Provider caps: rebuild any that changed; preserve existing
        # ones whose value is unchanged (so in-flight holders keep
        # their slot).
        for pid, cap in new_provider_caps.items():
            if _PROVIDER_CAPS.get(pid) != cap or pid not in _PROVIDER_SEMS:
                _PROVIDER_CAPS[pid] = cap
                _PROVIDER_SEMS[pid] = threading.Semaphore(cap)


def global_cap() -> int:
    _load_caps()
    return _GLOBAL_CAP


def provider_cap(provider_id: str) -> int:
    _load_caps()
    return _PROVIDER_CAPS.get(str(provider_id), _GLOBAL_CAP)


def bullet_rewrite_cap() -> int:
    """Per-task cap on concurrent bullet rewrites (asyncio.gather
    inside one ``rewrite_bullets`` call). The cap is layered on top
    of the global LLM gate so even if a task tries to rewrite N
    bullets concurrently the global throttle still bounds the
    provider load."""
    _load_caps()
    return _BULLET_CAP


def global_llm_semaphore() -> threading.Semaphore:
    _load_caps()
    assert _GLOBAL_SEM is not None  # noqa: S101 -- post-load invariant
    return _GLOBAL_SEM


def provider_semaphore(provider_id: str) -> threading.Semaphore:
    _load_caps()
    pid = str(provider_id)
    if pid not in _PROVIDER_SEMS:
        with _LOCK:
            if pid not in _PROVIDER_SEMS:
                _PROVIDER_CAPS[pid] = _GLOBAL_CAP
                _PROVIDER_SEMS[pid] = threading.Semaphore(_GLOBAL_CAP)
    return _PROVIDER_SEMS[pid]


@contextmanager
def llm_call_gate(provider_id: str) -> Iterator[None]:
    """Hold both the global and the per-provider semaphore for the
    duration of one LLM dispatch.

    Acquisition order is provider-first then global; this means a
    chain of fallback providers waits on their own provider gate
    independently but every concurrent LLM call still goes through
    the global cap.
    """
    prov_sem = provider_semaphore(provider_id)
    global_sem = global_llm_semaphore()
    prov_sem.acquire()
    try:
        global_sem.acquire()
        try:
            yield
        finally:
            global_sem.release()
    finally:
        prov_sem.release()


def reset_for_tests() -> None:
    """Drop the cached semaphores so tests can reload settings."""
    global _GLOBAL_SEM, _PROVIDER_SEMS, _PROVIDER_CAPS, _GLOBAL_CAP, _BULLET_CAP
    with _LOCK:
        _GLOBAL_SEM = None
        _PROVIDER_SEMS = {}
        _PROVIDER_CAPS = {}
        _GLOBAL_CAP = _DEFAULT_GLOBAL_MAX_CONCURRENT
        _BULLET_CAP = _DEFAULT_BULLET_REWRITES_PER_TASK


__all__ = [
    "bullet_rewrite_cap",
    "global_cap",
    "global_llm_semaphore",
    "llm_call_gate",
    "provider_cap",
    "provider_semaphore",
    "reset_for_tests",
]
