"""Smoke tests for the Phase 17.9.2 OpenAI-compatible providers.

DeepSeek / Moonshot / Qwen / xAI / Groq / Mistral / OpenRouter all
share :class:`src.providers.api_base.OpenAICompatibleProvider`, so
their behaviour is already covered by the OpenAI tests in
``test_providers_api.py``. These tests just verify the registration
contract: each class lands in the registry with a non-empty catalog,
its default_model is in that catalog, ids and env vars are distinct,
and the base URL points at the right vendor.

We use the production registry singleton -- ``reset_default_registry``
guarantees a clean rebuild even if other tests have already populated
the cache.
"""

from __future__ import annotations

import pytest

from src.providers.api_base import OpenAICompatibleProvider
from src.providers.base import AuthType
from src.providers.deepseek import DeepSeekProvider
from src.providers.groq import GroqProvider
from src.providers.mistral import MistralProvider
from src.providers.moonshot import MoonshotProvider
from src.providers.openrouter import OpenRouterProvider
from src.providers.qwen import QwenProvider
from src.providers.registry import get_registry, reset_default_registry
from src.providers.xai import XAIProvider

# (class, expected_id, expected_env_var, expected_base_url_host)
_NEW_PROVIDERS = [
    (DeepSeekProvider, "deepseek", "DEEPSEEK_API_KEY", "api.deepseek.com"),
    (MoonshotProvider, "moonshot", "MOONSHOT_API_KEY", "api.moonshot.cn"),
    (QwenProvider, "qwen", "DASHSCOPE_API_KEY", "dashscope.aliyuncs.com"),
    (XAIProvider, "xai", "XAI_API_KEY", "api.x.ai"),
    (GroqProvider, "groq", "GROQ_API_KEY", "api.groq.com"),
    (MistralProvider, "mistral", "MISTRAL_API_KEY", "api.mistral.ai"),
    (OpenRouterProvider, "openrouter", "OPENROUTER_API_KEY", "openrouter.ai"),
]


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    # Force the singleton to rebuild against the current builtin set;
    # otherwise a registry cached by an earlier test may be missing
    # one of the new ids if module-load order is unlucky.
    reset_default_registry()
    yield
    reset_default_registry()


@pytest.mark.parametrize("cls,pid,env,host", _NEW_PROVIDERS)
def test_provider_class_metadata(
    cls: type, pid: str, env: str, host: str
) -> None:
    assert cls.id == pid
    assert cls.api_key_env_var == env
    assert host in cls.default_base_url
    assert cls.auth_type is AuthType.API_KEY
    # Catalog must be non-empty and the default model must be in it so
    # the picker can highlight it without falling through to "Custom...".
    catalog_ids = {m.id for m in cls.KNOWN_MODELS}
    assert catalog_ids, f"{cls.__name__} ships an empty catalog"
    assert cls.default_model in catalog_ids, (
        f"{cls.__name__} default_model {cls.default_model!r} missing from catalog"
    )
    # Every entry must be unique by id.
    assert len(catalog_ids) == len(cls.KNOWN_MODELS), (
        f"{cls.__name__} has duplicate catalog ids"
    )
    # Sanity: all new providers go through the shared base so a future
    # refactor on OpenAICompatibleProvider reaches them uniformly.
    assert issubclass(cls, OpenAICompatibleProvider)


def test_new_providers_registered_in_singleton() -> None:
    registry = get_registry()
    ids = set(registry.ids())
    for _, pid, _, _ in _NEW_PROVIDERS:
        assert pid in ids, f"{pid!r} missing from registry"


def test_ids_and_env_vars_are_globally_unique() -> None:
    # If two providers ever collide on id or env var it silently
    # routes credentials to the wrong upstream. Cheap invariant to pin.
    registry = get_registry()
    ids = [p.id for p in registry.all()]
    assert len(ids) == len(set(ids)), f"duplicate provider ids: {ids}"

    env_vars = [
        getattr(p, "api_key_env_var", "")
        for p in registry.all()
        if getattr(p, "api_key_env_var", "")
    ]
    assert len(env_vars) == len(set(env_vars)), (
        f"duplicate api_key_env_var: {env_vars}"
    )


def test_public_view_surfaces_new_provider_catalog() -> None:
    registry = get_registry()
    by_id = {row["id"]: row for row in registry.public_view()}
    for _, pid, _, _ in _NEW_PROVIDERS:
        assert pid in by_id
        assert by_id[pid]["known_models"], f"{pid} public_view missing catalog"


def test_public_view_surfaces_api_key_format_hints() -> None:
    """Phase 17.9.13: every shipped API-key provider should expose a
    soft key-format hint (pattern + example) so the Connect dialog
    can warn on obvious typos before burning a network probe."""
    registry = get_registry()
    by_id = {row["id"]: row for row in registry.public_view()}
    expected_prefix = {
        "openai": "sk-",
        "anthropic": "sk-ant-",
        "gemini": "AIza",
        "deepseek": "sk-",
        "moonshot": "sk-",
        "qwen": "sk-",
        "xai": "xai-",
        "groq": "gsk_",
        "openrouter": "sk-or-",
    }
    for pid, prefix in expected_prefix.items():
        row = by_id[pid]
        assert row["api_key_pattern"], f"{pid} missing api_key_pattern"
        assert row["api_key_example"], f"{pid} missing api_key_example"
        assert row["api_key_example"].startswith(prefix), (
            f"{pid} api_key_example {row['api_key_example']!r} "
            f"doesn't lead with {prefix!r}"
        )
    # Mistral keys have no fixed prefix -- still exposed though.
    assert by_id["mistral"]["api_key_pattern"]
    assert by_id["mistral"]["api_key_example"]
