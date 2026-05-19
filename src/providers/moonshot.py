"""Moonshot AI (Kimi) provider (OpenAI-compatible Chat Completions).

Long-context models from moonshot.cn. Auth is ``Authorization: Bearer``,
endpoint shape is OpenAI's ``/v1/chat/completions``.
"""

from __future__ import annotations

from src.providers.api_base import OpenAICompatibleProvider
from src.providers.base import ModelInfo


class MoonshotProvider(OpenAICompatibleProvider):
    id = "moonshot"
    display_name = "Moonshot (Kimi)"
    description = "Moonshot AI / Kimi (long-context Chinese models)"
    install_hint = "Get an API key from https://platform.moonshot.cn/console/api-keys"
    api_key_env_var = "MOONSHOT_API_KEY"
    default_base_url = "https://api.moonshot.cn/v1"
    default_model = "moonshot-v1-32k"

    KNOWN_MODELS = (
        ModelInfo(
            id="moonshot-v1-8k",
            display_name="Moonshot v1 8K",
            context_window=8_192,
            tags=("cheap",),
        ),
        ModelInfo(
            id="moonshot-v1-32k",
            display_name="Moonshot v1 32K",
            context_window=32_768,
            tags=("balanced",),
        ),
        ModelInfo(
            id="moonshot-v1-128k",
            display_name="Moonshot v1 128K",
            context_window=128_000,
            tags=("long-context",),
        ),
        ModelInfo(
            id="kimi-k2",
            display_name="Kimi K2",
            context_window=128_000,
            tags=("smart", "long-context"),
        ),
    )
