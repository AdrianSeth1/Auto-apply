"""OpenRouter provider (OpenAI-compatible aggregator).

OpenRouter exposes hundreds of models under one key. KNOWN_MODELS only
seeds a handful of popular routes; the rest are discoverable through
the runtime ``/v1/models`` catalog endpoint (17.9.4).
"""

from __future__ import annotations

from src.providers.api_base import OpenAICompatibleProvider
from src.providers.base import ModelInfo


class OpenRouterProvider(OpenAICompatibleProvider):
    id = "openrouter"
    display_name = "OpenRouter"
    description = "OpenRouter aggregator (hundreds of models behind one key)"
    install_hint = "Get an API key from https://openrouter.ai/keys"
    api_key_env_var = "OPENROUTER_API_KEY"
    default_base_url = "https://openrouter.ai/api/v1"
    default_model = "anthropic/claude-sonnet-4.5"

    # OpenRouter has 200+ models; we seed the popular routing slugs and
    # rely on the dynamic /v1/models endpoint (17.9.4) for the rest.
    KNOWN_MODELS = (
        ModelInfo(
            id="anthropic/claude-haiku-4.5",
            display_name="Claude Haiku 4.5",
            context_window=200_000,
            tags=("fast", "cheap"),
        ),
        ModelInfo(
            id="anthropic/claude-sonnet-4.5",
            display_name="Claude Sonnet 4.5",
            context_window=200_000,
            tags=("balanced",),
        ),
        ModelInfo(
            id="anthropic/claude-opus-4.7",
            display_name="Claude Opus 4.7",
            context_window=200_000,
            tags=("smart",),
        ),
        ModelInfo(
            id="openai/gpt-4o-mini",
            display_name="GPT-4o mini",
            context_window=128_000,
            tags=("fast", "cheap"),
        ),
        ModelInfo(
            id="openai/gpt-4.1",
            display_name="GPT-4.1",
            context_window=1_000_000,
            tags=("smart", "long-context"),
        ),
        ModelInfo(
            id="google/gemini-2.5-flash",
            display_name="Gemini 2.5 Flash",
            context_window=1_000_000,
            tags=("balanced", "long-context"),
        ),
        ModelInfo(
            id="google/gemini-2.5-pro",
            display_name="Gemini 2.5 Pro",
            context_window=2_000_000,
            tags=("smart", "long-context"),
        ),
        ModelInfo(
            id="deepseek/deepseek-chat",
            display_name="DeepSeek Chat",
            context_window=128_000,
            tags=("balanced", "cheap"),
        ),
        ModelInfo(
            id="meta-llama/llama-3.3-70b-instruct",
            display_name="Llama 3.3 70B",
            context_window=128_000,
            tags=("balanced",),
        ),
        ModelInfo(
            id="x-ai/grok-4",
            display_name="Grok 4",
            context_window=256_000,
            tags=("smart",),
        ),
    )
