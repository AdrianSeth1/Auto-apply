"""Phase 18.5: parallel-fanout tests for resume bullets + JD batch parsing.

We stub the actual LLM calls so the tests don't need a provider; the
focus is the fan-out / order-preservation / concurrency-cap behaviour.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.utils import parallelism


@pytest.fixture(autouse=True)
def _bullet_cap_2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin parallelism caps so the fan-out test is deterministic."""
    monkeypatch.setattr(
        parallelism,
        "load_config",
        lambda: {
            "parallelism": {
                "llm": {"max_concurrent_global": 100},
                "bullet_rewrites": {"max_concurrent_per_task": 2},
            }
        },
    )
    parallelism.reset_for_tests()
    yield
    parallelism.reset_for_tests()


def test_rewrite_bullets_runs_concurrently_and_preserves_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 18.5: bullets within one call fan out via asyncio.gather
    and the returned dict preserves the input ordering even though
    they finish in arbitrary order."""
    from src.generation.resume_builder import BulletRewriteResult, rewrite_bullets

    starts: list[float] = []

    def fake_single(bullet: str, _keywords: str, *, mode: str = "balanced"):
        starts.append(time.monotonic())
        # Sleep just long enough that two concurrent calls overlap.
        time.sleep(0.05)
        return BulletRewriteResult(rewritten_bullet=f"X-{bullet}")

    monkeypatch.setattr(
        "src.generation.resume_builder._rewrite_single_bullet", fake_single
    )

    start = time.monotonic()
    out = rewrite_bullets(
        {"company-a": ["b1", "b2", "b3", "b4"]},
        jd_tags=["python"],
    )
    elapsed = time.monotonic() - start

    # Order preserved.
    assert out["company-a"] == ["X-b1", "X-b2", "X-b3", "X-b4"]
    # With per-task cap=2 and four 50ms bullets the sequential lower
    # bound is ~200ms; concurrent should land near ~100ms. Allow
    # generous slack for CI / Windows scheduler jitter.
    assert elapsed < 0.18, f"rewrite_bullets did not parallelise (elapsed={elapsed:.3f}s)"


def test_parse_requirements_batch_preserves_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``parse_requirements_batch`` is an async fan-out helper for
    search post-processing. We stub ``parse_requirements`` and assert
    the i-th output corresponds to the i-th input regardless of
    completion order."""
    from src.intake import jd_parser
    from src.intake.schema import JobRequirements

    def fake_parse(description: str, use_llm: bool = True) -> JobRequirements:
        return JobRequirements(keywords=[f"kw-{description}"])

    monkeypatch.setattr(jd_parser, "parse_requirements", fake_parse)

    descriptions = ["foo", "bar", None, "baz", ""]
    results = asyncio.run(jd_parser.parse_requirements_batch(descriptions))
    assert len(results) == len(descriptions)
    assert results[0].keywords == ["kw-foo"]
    assert results[1].keywords == ["kw-bar"]
    # Empty / None descriptions short-circuit to a default JobRequirements.
    assert results[2].keywords == []
    assert results[3].keywords == ["kw-baz"]
    assert results[4].keywords == []
