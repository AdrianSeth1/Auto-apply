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
    description = "DeepSeek Chat Completions (deepseek-chat, deepseek-reasoner)"
    install_hint = "Get an API key from https://platform.deepseek.com/api_keys"
    api_key_env_var = "DEEPSEEK_API_KEY"
    default_base_url = "https://api.deepseek.com/v1"
    default_model = "deepseek-chat"

    KNOWN_MODELS = (
        ModelInfo(
            id="deepseek-chat",
            display_name="DeepSeek Chat (V3)",
            context_window=128_000,
            max_output_tokens=8_192,
            tags=("balanced", "cheap"),
        ),
        ModelInfo(
            id="deepseek-reasoner",
            display_name="DeepSeek Reasoner (R1)",
            context_window=128_000,
            max_output_tokens=8_192,
            tags=("reasoning",),
        ),
    )
