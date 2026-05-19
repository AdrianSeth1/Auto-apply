"""OpenAI Chat Completions provider.

The REST shape (Bearer auth + ``/v1/chat/completions``) is shared with
DeepSeek, Moonshot, Qwen, OpenRouter, xAI, Groq, Mistral, and others;
the common code now lives in
:class:`src.providers.api_base.OpenAICompatibleProvider`. This module
just pins the OpenAI-specific defaults and curated model catalog.

We deliberately do NOT take a hard dependency on the ``openai`` SDK --
the documented v1 REST surface is stable enough and avoids the SDK's
breaking-change cadence.
"""

from __future__ import annotations

from src.providers.api_base import OpenAICompatibleProvider
from src.providers.base import ModelInfo

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIProvider(OpenAICompatibleProvider):
    id = "openai"
    display_name = "OpenAI"
    description = "OpenAI Chat Completions (gpt-4o, gpt-4o-mini, o-series, ...)"
    install_hint = "Get an API key from https://platform.openai.com/api-keys"
    api_key_env_var = "OPENAI_API_KEY"
    default_base_url = DEFAULT_BASE_URL
    default_model = DEFAULT_MODEL

    KNOWN_MODELS = (
        ModelInfo(
            id="gpt-4o-mini",
            display_name="GPT-4o mini",
            context_window=128_000,
            max_output_tokens=16_384,
            tags=("fast", "cheap"),
        ),
        ModelInfo(
            id="gpt-4o",
            display_name="GPT-4o",
            context_window=128_000,
            max_output_tokens=16_384,
            tags=("balanced", "vision"),
        ),
        ModelInfo(
            id="gpt-4.1-mini",
            display_name="GPT-4.1 mini",
            context_window=1_000_000,
            max_output_tokens=32_768,
            tags=("fast", "long-context"),
        ),
        ModelInfo(
            id="gpt-4.1",
            display_name="GPT-4.1",
            context_window=1_000_000,
            max_output_tokens=32_768,
            tags=("smart", "long-context"),
        ),
        ModelInfo(
            id="o4-mini",
            display_name="o4 mini (reasoning)",
            context_window=200_000,
            max_output_tokens=100_000,
            tags=("reasoning", "fast"),
        ),
        ModelInfo(
            id="o3",
            display_name="o3 (reasoning)",
            context_window=200_000,
            max_output_tokens=100_000,
            tags=("reasoning", "smart"),
        ),
    )
