"""Mistral provider (OpenAI-compatible Chat Completions)."""

from __future__ import annotations

from src.providers.api_base import OpenAICompatibleProvider
from src.providers.base import ModelInfo


class MistralProvider(OpenAICompatibleProvider):
    id = "mistral"
    display_name = "Mistral"
    description = "Mistral AI Chat Completions (Large 3, Medium 3.5, Codestral, Magistral)"
    install_hint = "Get an API key from https://console.mistral.ai/api-keys/"
    api_key_env_var = "MISTRAL_API_KEY"
    default_base_url = "https://api.mistral.ai/v1"
    default_model = "mistral-medium-latest"
    # Mistral keys are 32-char alphanumeric with no fixed prefix.
    api_key_pattern = r"^[A-Za-z0-9]{20,}$"
    api_key_example = "a 32-character alphanumeric key"

    # Curated from mistral.ai/models on 2026-05-19. Mistral Large 3
    # (Dec 2025) bumps the flagship to a 256k MoE; Medium 3.5
    # (Apr 2026) is the recommended balanced default. Magistral and
    # Devstral are the reasoning / coding-agent specialists.
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
            display_name="Mistral Small 3.1",
            context_window=128_000,
            tags=("balanced", "cheap"),
        ),
        ModelInfo(
            id="mistral-medium-latest",
            display_name="Mistral Medium 3.5",
            context_window=128_000,
            tags=("balanced",),
        ),
        ModelInfo(
            id="mistral-large-latest",
            display_name="Mistral Large 3",
            context_window=256_000,
            tags=("smart", "flagship"),
        ),
        ModelInfo(
            id="magistral-medium-latest",
            display_name="Magistral Medium (reasoning)",
            context_window=128_000,
            tags=("reasoning",),
        ),
        ModelInfo(
            id="codestral-latest",
            display_name="Codestral 25.08",
            context_window=256_000,
            tags=("code",),
        ),
        ModelInfo(
            id="devstral-2",
            display_name="Devstral 2 (coding agent)",
            context_window=256_000,
            tags=("code", "agent"),
        ),
    )
