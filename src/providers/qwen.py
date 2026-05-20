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

    # Curated from Alibaba Cloud Model Studio docs on 2026-05-19.
    # Qwen-Turbo / Plus / Max are the stable production aliases that
    # automatically point at the current best snapshot under that
    # cost tier; the explicit qwen3-* ids are also pinned for users
    # who want a specific generation.
    KNOWN_MODELS = (
        ModelInfo(
            id="qwen-turbo",
            display_name="Qwen Turbo",
            context_window=1_000_000,
            tags=("fast", "cheap"),
        ),
        ModelInfo(
            id="qwen-plus",
            display_name="Qwen Plus",
            context_window=131_072,
            tags=("balanced",),
        ),
        ModelInfo(
            id="qwen3-max",
            display_name="Qwen3 Max",
            context_window=262_144,
            tags=("smart", "flagship"),
        ),
        ModelInfo(
            id="qwen3.5-flash",
            display_name="Qwen3.5 Flash",
            context_window=131_072,
            tags=("fast",),
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
