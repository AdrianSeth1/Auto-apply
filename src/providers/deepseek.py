"""DeepSeek provider (OpenAI-compatible Chat Completions).

DeepSeek serves an OpenAI-shaped ``/v1/chat/completions`` endpoint with
``Authorization: Bearer`` auth, so all the real work lives in
:class:`src.providers.api_base.OpenAICompatibleProvider`. This file
just pins the defaults + curated catalog.
"""

from __future__ import annotations

from src.providers.api_base import OpenAICompatibleProvider
from src.providers.base import ModelInfo


class DeepSeekProvider(OpenAICompatibleProvider):
    id = "deepseek"
    display_name = "DeepSeek"
    description = "DeepSeek V4 family (1M-token context, native thinking mode)"
    install_hint = "Get an API key from https://platform.deepseek.com/api_keys"
    api_key_env_var = "DEEPSEEK_API_KEY"
    default_base_url = "https://api.deepseek.com/v1"
    default_model = "deepseek-v4-flash"
    api_key_pattern = r"^sk-[A-Za-z0-9]{20,}$"
    api_key_example = "sk-..."

    # Curated from api-docs.deepseek.com/quick_start/pricing on
    # 2026-05-19. The legacy aliases `deepseek-chat` and
    # `deepseek-reasoner` still map to v4-flash's non-thinking /
    # thinking modes but DeepSeek scheduled them for deprecation on
    # 2026-07-24, so new users should land on the explicit v4 ids.
    KNOWN_MODELS = (
        ModelInfo(
            id="deepseek-v4-flash",
            display_name="DeepSeek V4 Flash",
            context_window=1_000_000,
            max_output_tokens=384_000,
            tags=("balanced", "cheap", "long-context"),
        ),
        ModelInfo(
            id="deepseek-v4-pro",
            display_name="DeepSeek V4 Pro",
            context_window=1_000_000,
            max_output_tokens=384_000,
            tags=("smart", "reasoning", "long-context"),
        ),
        ModelInfo(
            id="deepseek-chat",
            display_name="deepseek-chat (legacy alias)",
            context_window=128_000,
            max_output_tokens=8_192,
            tags=("legacy",),
        ),
        ModelInfo(
            id="deepseek-reasoner",
            display_name="deepseek-reasoner (legacy alias)",
            context_window=128_000,
            max_output_tokens=8_192,
            tags=("legacy", "reasoning"),
        ),
    )
