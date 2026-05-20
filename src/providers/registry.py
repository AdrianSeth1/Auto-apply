"""Provider registry.

The registry is the single place that knows the full set of supported
providers. CLI, Web UI, and the agent loop all consult it rather than
hard-coding provider ids. New providers register themselves here so
adding one is a single import.

A process-wide instance is exposed via :func:`get_registry` to keep
imports cheap; tests construct their own :class:`ProviderRegistry`
with a stub :class:`CredentialStore` to avoid touching real disk.

Phase 17.9.6 adds user-defined providers. Users can declare additional
OpenAI-compatible providers in ``config/settings.yaml`` under
``llm.custom_providers``; we synthesise an :class:`OpenAICompatibleProvider`
subclass at registry-init time and register it alongside the builtins.
Restart-only: hot reload would require teaching every caching layer
(health monitor, settings page) to forget stale rows -- a future
refactor's problem.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from src.providers.base import LLMProvider, ProviderError
from src.providers.store import CredentialStore

logger = logging.getLogger("autoapply.providers.registry")


class ProviderRegistry:
    """Holds provider classes plus a shared credential store."""

    def __init__(self, store: CredentialStore | None = None) -> None:
        self._store = store or CredentialStore()
        self._classes: dict[str, type[LLMProvider]] = {}
        self._instances: dict[str, LLMProvider] = {}

    # ----- registration -----

    def register(self, cls: type[LLMProvider]) -> None:
        if not getattr(cls, "id", ""):
            raise ProviderError(
                f"Provider class {cls!r} must define a non-empty `id`."
            )
        if cls.id in self._classes:
            raise ProviderError(f"Provider id {cls.id!r} already registered.")
        self._classes[cls.id] = cls

    def register_all(self, classes: Iterable[type[LLMProvider]]) -> None:
        for cls in classes:
            self.register(cls)

    # ----- lookup -----

    def ids(self) -> list[str]:
        return sorted(self._classes)

    def get(self, provider_id: str) -> LLMProvider:
        if provider_id not in self._classes:
            raise ProviderError(f"Unknown provider {provider_id!r}.")
        if provider_id not in self._instances:
            self._instances[provider_id] = self._classes[provider_id](store=self._store)
        return self._instances[provider_id]

    def maybe_get(self, provider_id: str) -> LLMProvider | None:
        try:
            return self.get(provider_id)
        except ProviderError:
            return None

    def all(self) -> list[LLMProvider]:
        return [self.get(pid) for pid in self.ids()]

    def configured(self) -> list[LLMProvider]:
        return [p for p in self.all() if p.is_configured()]

    @property
    def store(self) -> CredentialStore:
        return self._store

    def public_view(self) -> list[dict[str, Any]]:
        return [p.public_view() for p in self.all()]


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------

_default_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    """Lazy singleton populated by :func:`_register_builtins` on first call.

    The lazy import in ``_register_builtins`` keeps the base modules free
    of provider-specific deps (httpx clients are imported only when a
    provider is actually instantiated).
    """
    global _default_registry
    if _default_registry is None:
        registry = ProviderRegistry()
        _register_builtins(registry)
        # Phase 17.9.6: user-defined custom providers from settings.yaml.
        # Loaded AFTER builtins so id collisions log a clear warning
        # and the builtin wins (we never silently shadow a first-party
        # provider with user config). Settings reads are best-effort:
        # a parse error here must NOT prevent the registry from
        # initialising.
        try:
            _register_custom_providers(registry)
        except Exception as exc:  # noqa: BLE001 -- never break startup
            logger.warning(
                "Skipping llm.custom_providers (config error): %s", exc
            )
        _default_registry = registry
    return _default_registry


def _register_builtins(registry: ProviderRegistry) -> None:
    """Register the providers shipped with AutoApply.

    Lazy imports keep the dependency surface small until callers
    actually need a provider.
    """
    from src.providers.anthropic import AnthropicProvider  # noqa: PLC0415
    from src.providers.claude_cli import ClaudeCliProvider  # noqa: PLC0415
    from src.providers.codex import CodexCliProvider  # noqa: PLC0415
    from src.providers.deepseek import DeepSeekProvider  # noqa: PLC0415
    from src.providers.gemini import GeminiProvider  # noqa: PLC0415
    from src.providers.groq import GroqProvider  # noqa: PLC0415
    from src.providers.mistral import MistralProvider  # noqa: PLC0415
    from src.providers.moonshot import MoonshotProvider  # noqa: PLC0415
    from src.providers.ollama import OllamaProvider  # noqa: PLC0415
    from src.providers.openai import OpenAIProvider  # noqa: PLC0415
    from src.providers.openrouter import OpenRouterProvider  # noqa: PLC0415
    from src.providers.qwen import QwenProvider  # noqa: PLC0415
    from src.providers.xai import XAIProvider  # noqa: PLC0415

    # First-party REST providers (each owns its own client / response shape).
    registry.register(OpenAIProvider)
    registry.register(AnthropicProvider)
    registry.register(GeminiProvider)
    # Phase 17.9: OpenAI-compatible providers. All share the chat-completions
    # shape via OpenAICompatibleProvider; each subclass just pins defaults
    # + a curated KNOWN_MODELS catalog. Registering them all unconditionally
    # is cheap -- the registry instantiates lazily on first lookup.
    registry.register(DeepSeekProvider)
    registry.register(MoonshotProvider)
    registry.register(QwenProvider)
    registry.register(XAIProvider)
    registry.register(GroqProvider)
    registry.register(MistralProvider)
    registry.register(OpenRouterProvider)
    # Local self-hosted: keyless by default (see allow_empty_key).
    registry.register(OllamaProvider)
    # Codex and Claude are both subprocess wrappers around an
    # already-installed agent CLI; neither owns its OAuth flow. A
    # future native CodexOAuthProvider would live alongside these,
    # not replace them.
    registry.register(CodexCliProvider)
    registry.register(ClaudeCliProvider)


def reset_default_registry() -> None:
    """Drop the cached singleton. Used by tests that mutate registration."""
    global _default_registry
    _default_registry = None


# ---------------------------------------------------------------------------
# Phase 17.9.6 -- user-defined custom providers
# ---------------------------------------------------------------------------


# Minimal identifier shape: lowercase letters, digits, hyphens. Keeps URLs
# clean and prevents clashes with API path segments.
_VALID_ID_PATTERN = r"^[a-z][a-z0-9-]{0,63}$"


def _register_custom_providers(registry: ProviderRegistry) -> None:
    """Synthesise + register OpenAI-compatible providers from settings.yaml.

    Reads ``llm.custom_providers`` (a list of entries) and dynamically
    builds a subclass per entry. Validation errors on a single entry
    log a warning and skip that entry; we never raise from here.
    """
    import re  # noqa: PLC0415
    from typing import Any as _Any  # noqa: PLC0415

    from src.core.config import load_raw_config  # noqa: PLC0415
    from src.providers.api_base import OpenAICompatibleProvider  # noqa: PLC0415
    from src.providers.base import ModelInfo  # noqa: PLC0415

    config = load_raw_config()
    llm = config.get("llm", {}) if isinstance(config, dict) else {}
    entries = llm.get("custom_providers") if isinstance(llm, dict) else None
    if not isinstance(entries, list) or not entries:
        return

    id_re = re.compile(_VALID_ID_PATTERN)
    seen: set[str] = set(registry.ids())

    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            logger.warning(
                "llm.custom_providers[%d] is not a mapping; skipping.", index
            )
            continue
        pid = str(raw.get("id", "")).strip()
        if not id_re.match(pid):
            logger.warning(
                "llm.custom_providers[%d]: id %r is missing or "
                "doesn't match %s; skipping.",
                index,
                pid,
                _VALID_ID_PATTERN,
            )
            continue
        if pid in seen:
            logger.warning(
                "llm.custom_providers[%d]: id %r collides with an "
                "existing provider; skipping.",
                index,
                pid,
            )
            continue
        base_url = str(raw.get("base_url", "")).strip().rstrip("/")
        if not base_url:
            logger.warning(
                "llm.custom_providers[%d] (%s): base_url is required; skipping.",
                index,
                pid,
            )
            continue
        default_model = str(raw.get("default_model", "")).strip()
        if not default_model:
            logger.warning(
                "llm.custom_providers[%d] (%s): default_model is required; skipping.",
                index,
                pid,
            )
            continue

        catalog: list[ModelInfo] = []
        raw_models = raw.get("models")
        if isinstance(raw_models, list):
            for m in raw_models:
                if isinstance(m, str) and m.strip():
                    catalog.append(ModelInfo(id=m.strip()))
                elif isinstance(m, dict) and isinstance(m.get("id"), str):
                    catalog.append(
                        ModelInfo(
                            id=str(m["id"]).strip(),
                            display_name=str(m.get("display_name", "")).strip(),
                            context_window=(
                                int(m["context_window"])
                                if isinstance(m.get("context_window"), int)
                                else None
                            ),
                            max_output_tokens=(
                                int(m["max_output_tokens"])
                                if isinstance(m.get("max_output_tokens"), int)
                                else None
                            ),
                            supports_json=bool(m.get("supports_json", True)),
                            tags=tuple(
                                t for t in m.get("tags", []) if isinstance(t, str)
                            ),
                        )
                    )
        # Make sure default_model is in the catalog so the picker
        # highlights it; users may have forgotten to list it explicitly.
        if not any(entry.id == default_model for entry in catalog):
            catalog.insert(
                0, ModelInfo(id=default_model, display_name=default_model)
            )

        display_name = str(raw.get("display_name", pid)).strip() or pid
        description = str(raw.get("description", "")).strip()
        env_var = str(raw.get("api_key_env", "")).strip()
        install_hint = str(raw.get("install_hint", "")).strip()
        allow_empty_key = bool(raw.get("allow_empty_key", False))

        attrs: dict[str, _Any] = {
            "id": pid,
            "display_name": display_name,
            "description": description or f"User-defined provider: {display_name}",
            "install_hint": install_hint,
            "api_key_env_var": env_var,
            "default_base_url": base_url,
            "default_model": default_model,
            "allow_empty_key": allow_empty_key,
            "KNOWN_MODELS": tuple(catalog),
        }
        # Dynamic class name keeps stack traces readable when an upstream
        # 4xx surfaces from this provider.
        cls_name = "CustomProvider_" + re.sub(r"[^A-Za-z0-9]+", "_", pid)
        provider_cls = type(cls_name, (OpenAICompatibleProvider,), attrs)
        try:
            registry.register(provider_cls)
        except ProviderError as exc:
            logger.warning(
                "llm.custom_providers[%d] (%s): registration failed: %s",
                index,
                pid,
                exc,
            )
            continue
        seen.add(pid)
        logger.info(
            "Registered custom provider %r -> %s (%d catalog entries)",
            pid,
            base_url,
            len(catalog),
        )
