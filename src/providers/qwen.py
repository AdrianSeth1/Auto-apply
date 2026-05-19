"""Alibaba Qwen (DashScope) provider (OpenAI-compatible Chat Completions).

DashScope exposes an OpenAI-compatible base URL alongside its native
DashScope API; we use the OpenAI-compat one so this drops into
``OpenAICompatibleProvider`` cleanly.
"""

from __future__ import annotations

from src.providers.api_base import OpenAICompatibleProvider
from src.providers.base import ModelInfo


class QwenProvider(OpenAICompatibleProvider):
    id = "qwen"
    display_name = "Qwen (DashScope)"
    description = "Alibaba Tongyi Qwen via DashScope's OpenAI-compatible endpoint"
    install_hint = (
        "Get an API key from https://bailian.console.aliyun.com/?apiKey=1"
    )
    api_key_env_var = "DASHSCOPE_API_KEY"
    default_base_url = (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    default_model = "qwen-plus"

    KNOWN_MODELS = (
        ModelInfo(
            id="qwen-turbo",
            display_name="Qwen Turbo",
            context_window=128_000,
            tags=("fast", "cheap"),
        ),
        ModelInfo(
            id="qwen-plus",
            display_name="Qwen Plus",
            context_window=128_000,
            tags=("balanced",),
        ),
        ModelInfo(
            id="qwen-max",
            display_name="Qwen Max",
            context_window=32_000,
            tags=("smart",),
        ),
        ModelInfo(
            id="qwen-long",
            display_name="Qwen Long",
            context_window=10_000_000,
            tags=("long-context",),
        ),
        ModelInfo(
            id="qwen3-coder-plus",
            display_name="Qwen3 Coder Plus",
            context_window=1_000_000,
            tags=("code", "long-context"),
        ),
    )
