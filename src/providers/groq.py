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
    default_model = "openai/gpt-oss-120b"
    api_key_pattern = r"^gsk_[A-Za-z0-9]{20,}$"
    api_key_example = "gsk_..."

    # Curated from console.groq.com/docs/models on 2026-05-19. Groq's
    # 2026-03-23 deprecation moved moonshotai/kimi-k2-instruct-0905
    # towards openai/gpt-oss-120b as the high-quality default.
    KNOWN_MODELS = (
        ModelInfo(
            id="openai/gpt-oss-20b",
            display_name="GPT-OSS 20B (fastest)",
            context_window=131_072,
            tags=("fast", "cheap"),
        ),
        ModelInfo(
            id="llama-3.1-8b-instant",
            display_name="Llama 3.1 8B Instant",
            context_window=131_072,
            tags=("fast", "cheap"),
        ),
        ModelInfo(
            id="llama-3.3-70b-versatile",
            display_name="Llama 3.3 70B Versatile",
            context_window=131_072,
            tags=("balanced",),
        ),
        ModelInfo(
            id="openai/gpt-oss-120b",
            display_name="GPT-OSS 120B",
            context_window=131_072,
            tags=("smart", "balanced"),
        ),
        ModelInfo(
            id="groq/compound",
            display_name="Groq Compound (built-in tools)",
            context_window=131_072,
            tags=("agent", "tools"),
        ),
    )
