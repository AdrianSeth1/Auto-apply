"""Groq provider (OpenAI-compatible, very high throughput).

Groq's value is throughput, not exotic models -- it hosts open-weight
models on custom LPU silicon. Useful for cheap small-tier work in
17.9.5 (matching / classification) where latency matters and the
model just needs to be passable.
"""

from __future__ import annotations

from src.providers.api_base import OpenAICompatibleProvider
from src.providers.base import ModelInfo


class GroqProvider(OpenAICompatibleProvider):
    id = "groq"
    display_name = "Groq"
    description = "Groq LPU inference (open-weight models, very high tok/s)"
    install_hint = "Get an API key from https://console.groq.com/keys"
    api_key_env_var = "GROQ_API_KEY"
    default_base_url = "https://api.groq.com/openai/v1"
    default_model = "llama-3.3-70b-versatile"

    KNOWN_MODELS = (
        ModelInfo(
            id="llama-3.1-8b-instant",
            display_name="Llama 3.1 8B Instant",
            context_window=128_000,
            tags=("fast", "cheap"),
        ),
        ModelInfo(
            id="llama-3.3-70b-versatile",
            display_name="Llama 3.3 70B Versatile",
            context_window=128_000,
            tags=("balanced",),
        ),
        ModelInfo(
            id="qwen/qwen3-32b",
            display_name="Qwen3 32B",
            context_window=128_000,
            tags=("balanced",),
        ),
        ModelInfo(
            id="openai/gpt-oss-120b",
            display_name="GPT-OSS 120B",
            context_window=128_000,
            tags=("smart",),
        ),
    )
