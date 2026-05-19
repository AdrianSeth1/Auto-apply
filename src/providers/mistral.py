"""Mistral provider (OpenAI-compatible Chat Completions)."""

from __future__ import annotations

from src.providers.api_base import OpenAICompatibleProvider
from src.providers.base import ModelInfo


class MistralProvider(OpenAICompatibleProvider):
    id = "mistral"
    display_name = "Mistral"
    description = "Mistral AI Chat Completions (mistral-small / large / codestral)"
    install_hint = "Get an API key from https://console.mistral.ai/api-keys/"
    api_key_env_var = "MISTRAL_API_KEY"
    default_base_url = "https://api.mistral.ai/v1"
    default_model = "mistral-small-latest"

    KNOWN_MODELS = (
        ModelInfo(
            id="ministral-3b-latest",
            display_name="Ministral 3B",
            context_window=128_000,
            tags=("fast", "cheap"),
        ),
        ModelInfo(
            id="ministral-8b-latest",
            display_name="Ministral 8B",
            context_window=128_000,
            tags=("fast",),
        ),
        ModelInfo(
            id="mistral-small-latest",
            display_name="Mistral Small",
            context_window=128_000,
            tags=("balanced",),
        ),
        ModelInfo(
            id="mistral-medium-latest",
            display_name="Mistral Medium",
            context_window=128_000,
            tags=("balanced",),
        ),
        ModelInfo(
            id="mistral-large-latest",
            display_name="Mistral Large",
            context_window=128_000,
            tags=("smart",),
        ),
        ModelInfo(
            id="codestral-latest",
            display_name="Codestral",
            context_window=256_000,
            tags=("code",),
        ),
    )
