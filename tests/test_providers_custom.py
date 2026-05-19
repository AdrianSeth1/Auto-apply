"""Tests for Phase 17.9.6 user-defined custom providers.

Custom providers come from ``config/settings.yaml`` ``llm.custom_providers``.
We exercise the loader by monkeypatching ``src.core.config.load_raw_config``
and then asking ``get_registry()`` to rebuild from scratch -- this is
the same path that runs at process start.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from src.providers.api_base import OpenAICompatibleProvider
from src.providers.registry import get_registry, reset_default_registry


def _patch_config(monkeypatch: pytest.MonkeyPatch, llm_section: dict[str, Any]) -> None:
    def _fake(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"llm": llm_section}

    monkeypatch.setattr("src.core.config.load_raw_config", _fake)
    reset_default_registry()


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_default_registry()
    yield
    reset_default_registry()


class TestCustomProviderRegistration:
    def test_no_custom_section_is_a_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, {})
        registry = get_registry()
        # Just confirm the builtins still loaded -- no crash, no surprises.
        assert "openai" in registry.ids()

    def test_valid_entry_registers_subclass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(
            monkeypatch,
            {
                "custom_providers": [
                    {
                        "id": "laozhang",
                        "display_name": "LaoZhang Proxy",
                        "base_url": "https://api.laozhang.ai/v1",
                        "default_model": "gpt-4o-mini",
                        "api_key_env": "LAOZHANG_API_KEY",
                        "description": "Third-party OpenAI proxy",
                        "models": [
                            {
                                "id": "gpt-4o-mini",
                                "display_name": "GPT-4o mini (proxy)",
                                "context_window": 128000,
                            },
                            "claude-sonnet-4-5",  # plain string also accepted
                        ],
                    }
                ]
            },
        )
        registry = get_registry()
        assert "laozhang" in registry.ids()
        provider = registry.get("laozhang")
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.display_name == "LaoZhang Proxy"
        assert provider.default_base_url == "https://api.laozhang.ai/v1"
        assert provider.default_model == "gpt-4o-mini"
        assert provider.api_key_env_var == "LAOZHANG_API_KEY"
        # Catalog contains both the structured entry and the string-form
        # shorthand.
        ids = {m.id for m in provider.KNOWN_MODELS}
        assert ids == {"gpt-4o-mini", "claude-sonnet-4-5"}

    def test_default_model_auto_added_when_missing_from_catalog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(
            monkeypatch,
            {
                "custom_providers": [
                    {
                        "id": "myllm",
                        "base_url": "https://example.com/v1",
                        "default_model": "house-special",
                        "models": [{"id": "another-model"}],
                    }
                ]
            },
        )
        provider = get_registry().get("myllm")
        ids = [m.id for m in provider.KNOWN_MODELS]
        # default_model is prepended so the picker can highlight it.
        assert ids[0] == "house-special"
        assert "another-model" in ids

    def test_allow_empty_key_propagated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(
            monkeypatch,
            {
                "custom_providers": [
                    {
                        "id": "self-hosted",
                        "base_url": "https://my-vllm.internal/v1",
                        "default_model": "Qwen/Qwen2.5-32B-Instruct",
                        "allow_empty_key": True,
                    }
                ]
            },
        )
        provider = get_registry().get("self-hosted")
        assert provider.allow_empty_key is True


class TestCustomProviderValidation:
    def test_missing_base_url_skipped_with_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _patch_config(
            monkeypatch,
            {
                "custom_providers": [
                    {"id": "bad", "default_model": "x"}
                ]
            },
        )
        with caplog.at_level(logging.WARNING):
            registry = get_registry()
        assert "bad" not in registry.ids()
        assert any("base_url" in r.message for r in caplog.records)

    def test_missing_default_model_skipped_with_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _patch_config(
            monkeypatch,
            {
                "custom_providers": [
                    {"id": "bad2", "base_url": "https://example.com/v1"}
                ]
            },
        )
        with caplog.at_level(logging.WARNING):
            registry = get_registry()
        assert "bad2" not in registry.ids()
        assert any("default_model" in r.message for r in caplog.records)

    def test_invalid_id_format_skipped(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _patch_config(
            monkeypatch,
            {
                "custom_providers": [
                    {
                        "id": "Has Capitals AND SPACES",
                        "base_url": "https://example.com/v1",
                        "default_model": "x",
                    }
                ]
            },
        )
        with caplog.at_level(logging.WARNING):
            registry = get_registry()
        # The malformed id never makes it in.
        assert "Has Capitals AND SPACES" not in registry.ids()
        assert any(
            "doesn't match" in r.message or "missing" in r.message
            for r in caplog.records
        )

    def test_id_collision_with_builtin_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _patch_config(
            monkeypatch,
            {
                "custom_providers": [
                    {
                        "id": "openai",  # collides with builtin
                        "base_url": "https://my-fake.example.com/v1",
                        "default_model": "fake-model",
                    }
                ]
            },
        )
        with caplog.at_level(logging.WARNING):
            registry = get_registry()
        # Builtin wins; the registered class is still the original.
        provider = registry.get("openai")
        from src.providers.openai import OpenAIProvider  # noqa: PLC0415

        assert isinstance(provider, OpenAIProvider)
        assert any("collides" in r.message for r in caplog.records)

    def test_non_list_section_silently_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A user accidentally writing `custom_providers: nope` should
        # not crash startup.
        _patch_config(monkeypatch, {"custom_providers": "oops"})
        registry = get_registry()
        # Builtins still load.
        assert "openai" in registry.ids()
