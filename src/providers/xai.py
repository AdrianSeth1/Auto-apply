"""xAI Grok provider (OpenAI-compatible Chat Completions)."""

from __future__ import annotations

from src.providers.api_base import OpenAICompatibleProvider
from src.providers.base import ModelInfo


class XAIProvider(OpenAICompatibleProvider):
    id = "xai"
    display_name = "xAI Grok"
    description = "xAI Grok models via the OpenAI-compatible endpoint"
    install_hint = "Get an API key from https://console.x.ai/"
    api_key_env_var = "XAI_API_KEY"
    default_base_url = "https://api.x.ai/v1"
    default_model = "grok-4-fast"

    KNOWN_MODELS = (
        ModelInfo(
            id="grok-4-fast",
            display_name="Grok 4 Fast",
            context_window=256_000,
            tags=("fast", "balanced"),
        ),
        ModelInfo(
            id="grok-4",
            display_name="Grok 4",
            context_window=256_000,
            tags=("smart",),
        ),
        ModelInfo(
            id="grok-3-mini",
            display_name="Grok 3 mini",
            context_window=128_000,
            tags=("fast", "cheap"),
        ),
    )
