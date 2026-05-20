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
    default_model = "anthropic/claude-sonnet-4.6"

    # Curated 2026-05-19 against each upstream provider's own
    # 2026-current ids. OpenRouter has 200+ models -- this list is a
    # popular-route seed; the dynamic /v1/models endpoint (17.9.4)
    # surfaces the rest.
    KNOWN_MODELS = (
        ModelInfo(
            id="anthropic/claude-haiku-4.5",
            display_name="Claude Haiku 4.5",
            context_window=200_000,
            tags=("fast", "cheap"),
        ),
        ModelInfo(
            id="anthropic/claude-sonnet-4.6",
            display_name="Claude Sonnet 4.6",
            context_window=1_000_000,
            tags=("balanced",),
        ),
        ModelInfo(
            id="anthropic/claude-opus-4.7",
            display_name="Claude Opus 4.7",
            context_window=1_000_000,
            tags=("smart",),
        ),
        ModelInfo(
            id="openai/gpt-5.4-mini",
            display_name="GPT-5.4 mini",
            context_window=400_000,
            tags=("fast", "cheap"),
        ),
        ModelInfo(
            id="openai/gpt-5.5",
            display_name="GPT-5.5",
            context_window=1_000_000,
            tags=("smart", "flagship"),
        ),
        ModelInfo(
            id="google/gemini-3.5-flash",
            display_name="Gemini 3.5 Flash",
            context_window=1_048_576,
            tags=("balanced", "long-context"),
        ),
        ModelInfo(
            id="google/gemini-2.5-pro",
            display_name="Gemini 2.5 Pro",
            context_window=2_000_000,
            tags=("smart", "long-context"),
        ),
        ModelInfo(
            id="deepseek/deepseek-v4-flash",
            display_name="DeepSeek V4 Flash",
            context_window=1_000_000,
            tags=("balanced", "cheap"),
        ),
        ModelInfo(
            id="meta-llama/llama-3.3-70b-instruct",
            display_name="Llama 3.3 70B Instruct",
            context_window=128_000,
            tags=("balanced",),
        ),
        ModelInfo(
            id="moonshotai/kimi-k2.6",
            display_name="Kimi K2.6",
            context_window=256_000,
            tags=("smart", "long-context"),
        ),
        ModelInfo(
            id="x-ai/grok-4.3",
            display_name="Grok 4.3",
            context_window=1_000_000,
            tags=("smart",),
        ),
        ModelInfo(
            id="qwen/qwen3-max",
            display_name="Qwen3 Max",
            context_window=262_144,
            tags=("smart",),
        ),
    )
