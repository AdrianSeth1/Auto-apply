"""Phase 18.5: tests for the process-wide LLM rate-limit primitives.

The semaphores are :class:`threading.Semaphore` so the same instance
works for sync subprocess CLI calls, asyncio.to_thread fan-out, and
plain threadpool callers. These tests cover the resolution +
acquisition contract; integration with ``generate_text`` and
``rewrite_bullets`` lives in their own test modules.
"""

from __future__ import annotations

import threading
import time

import pytest

from src.utils import parallelism


@pytest.fixture(autouse=True)
def _reset_caps() -> None:
    parallelism.reset_for_tests()
    yield
    parallelism.reset_for_tests()


def test_defaults_used_when_settings_block_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parallelism, "load_config", lambda: {})
    parallelism.reset_for_tests()
    assert parallelism.global_cap() == 10
    assert parallelism.bullet_rewrite_cap() == 5


def test_global_cap_honours_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        parallelism,
        "load_config",
        lambda: {"parallelism": {"llm": {"max_concurrent_global": 3}}},
    )
    parallelism.reset_for_tests()
    assert parallelism.global_cap() == 3


def test_provider_cap_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        parallelism,
        "load_config",
        lambda: {
            "parallelism": {
                "llm": {"max_concurrent_global": 10},
                "provider": {"openai": {"max_concurrent": 2}},
            }
        },
    )
    parallelism.reset_for_tests()
    assert parallelism.provider_cap("openai") == 2
    # Unknown providers inherit the global cap.
    assert parallelism.provider_cap("anthropic") == 10


def test_bullet_rewrite_cap_honours_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        parallelism,
        "load_config",
        lambda: {
            "parallelism": {"bullet_rewrites": {"max_concurrent_per_task": 2}}
        },
    )
    parallelism.reset_for_tests()
    assert parallelism.bullet_rewrite_cap() == 2


def test_llm_call_gate_serialises_when_cap_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With max_concurrent_global=1 only one thread may hold the gate
    at a time. We assert by recording entry/exit order from two
    threads racing for the same provider."""
    monkeypatch.setattr(
        parallelism,
        "load_config",
        lambda: {"parallelism": {"llm": {"max_concurrent_global": 1}}},
    )
    parallelism.reset_for_tests()

    log: list[str] = []
    started = threading.Event()

    def _worker(label: str, hold_seconds: float) -> None:
        with parallelism.llm_call_gate("openai"):
            log.append(f"{label}:enter")
            started.set()
            time.sleep(hold_seconds)
            log.append(f"{label}:exit")

    t1 = threading.Thread(target=_worker, args=("a", 0.1))
    t2 = threading.Thread(target=_worker, args=("b", 0.05))
    t1.start()
    started.wait()
    t2.start()
    t1.join()
    t2.join()

    # The second thread cannot have entered until the first exited.
    assert log == ["a:enter", "a:exit", "b:enter", "b:exit"]


def test_llm_call_gate_allows_concurrency_up_to_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        parallelism,
        "load_config",
        lambda: {"parallelism": {"llm": {"max_concurrent_global": 2}}},
    )
    parallelism.reset_for_tests()

    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def _worker() -> None:
        nonlocal in_flight, peak
        with parallelism.llm_call_gate("openai"):
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.05)
            with lock:
                in_flight -= 1

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak <= 2
    assert peak >= 1
