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
    description = "Moonshot AI / Kimi K2.x family (256k context, MoE)"
    install_hint = "Get an API key from https://platform.moonshot.cn/console/api-keys"
    api_key_env_var = "MOONSHOT_API_KEY"
    default_base_url = "https://api.moonshot.cn/v1"
    default_model = "kimi-k2.6"

    # Curated from platform.kimi.ai/docs/models on 2026-05-19. The
    # base kimi-k2 series is being retired 2026-05-25 in favor of
    # kimi-k2.5/k2.6; the moonshot-v1-* generation models stay
    # available for shorter-context workflows.
    KNOWN_MODELS = (
        ModelInfo(
            id="moonshot-v1-32k",
            display_name="Moonshot v1 32K",
            context_window=32_768,
            tags=("balanced", "cheap"),
        ),
        ModelInfo(
            id="moonshot-v1-128k",
            display_name="Moonshot v1 128K",
            context_window=128_000,
            tags=("long-context",),
        ),
        ModelInfo(
            id="kimi-k2.5",
            display_name="Kimi K2.5",
            context_window=256_000,
            tags=("balanced", "long-context"),
        ),
        ModelInfo(
            id="kimi-k2.6",
            display_name="Kimi K2.6",
            context_window=256_000,
            tags=("smart", "long-context", "flagship"),
        ),
        ModelInfo(
            id="kimi-k2-thinking",
            display_name="Kimi K2 Thinking",
            context_window=256_000,
            tags=("reasoning", "long-context"),
        ),
        ModelInfo(
            id="kimi-k2-thinking-turbo",
            display_name="Kimi K2 Thinking Turbo",
            context_window=256_000,
            tags=("reasoning", "fast"),
        ),
    )
