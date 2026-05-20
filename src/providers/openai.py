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
DEFAULT_MODEL = "gpt-5.4-mini"


class OpenAIProvider(OpenAICompatibleProvider):
    id = "openai"
    display_name = "OpenAI"
    description = "OpenAI Chat Completions (GPT-5.x flagship + o-series reasoning)"
    install_hint = "Get an API key from https://platform.openai.com/api-keys"
    api_key_env_var = "OPENAI_API_KEY"
    default_base_url = DEFAULT_BASE_URL
    default_model = DEFAULT_MODEL
    # `sk-` is the historical prefix; `sk-proj-` and `sk-svcacct-`
    # appeared in 2024-2025 for project and service-account keys. The
    # pattern stays loose -- the upstream probe is the real validator.
    api_key_pattern = r"^sk-[A-Za-z0-9_-]{20,}$"
    api_key_example = "sk-..."

    # Curated from developers.openai.com/api/docs/models on 2026-05-19.
    # Legacy ids (gpt-4o, gpt-4.1, o4-mini original) still work on the
    # API even when retired from ChatGPT, but they're hidden from the
    # default dropdown to keep users on the live recommended set.
    KNOWN_MODELS = (
        ModelInfo(
            id="gpt-5.4-mini",
            display_name="GPT-5.4 mini",
            context_window=400_000,
            max_output_tokens=128_000,
            tags=("fast", "cheap"),
        ),
        ModelInfo(
            id="gpt-5.4",
            display_name="GPT-5.4",
            context_window=1_000_000,
            max_output_tokens=128_000,
            tags=("balanced",),
        ),
        ModelInfo(
            id="gpt-5.5",
            display_name="GPT-5.5",
            context_window=1_000_000,
            max_output_tokens=128_000,
            tags=("smart", "flagship"),
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
            tags=("reasoning",),
        ),
        ModelInfo(
            id="o3-pro",
            display_name="o3-pro (deep reasoning)",
            context_window=200_000,
            max_output_tokens=100_000,
            tags=("reasoning", "smart"),
        ),
    )
