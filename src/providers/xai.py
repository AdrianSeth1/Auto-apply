"""xAI Grok provider (OpenAI-compatible Chat Completions)."""

from __future__ import annotations

from src.providers.api_base import OpenAICompatibleProvider
from src.providers.base import ModelInfo


class XAIProvider(OpenAICompatibleProvider):
    id = "xai"
    display_name = "xAI Grok"
    description = "xAI Grok 4.x family (1M context, adjustable reasoning)"
    install_hint = "Get an API key from https://console.x.ai/"
    api_key_env_var = "XAI_API_KEY"
    default_base_url = "https://api.x.ai/v1"
    default_model = "grok-4.3"
    api_key_pattern = r"^xai-[A-Za-z0-9]{20,}$"
    api_key_example = "xai-..."

    # Curated from docs.x.ai/developers/models on 2026-05-19. Earlier
    # grok-4 / grok-4-fast / grok-code-fast-1 ids were retired
    # 2026-05-15 and now redirect to grok-4.3 pricing -- they're left
    # out of the picker to avoid confusion. grok-4.20 has separate
    # reasoning / non-reasoning checkpoint suffixes that may rotate;
    # we pin the 2026-03-09 snapshot ids xAI currently exposes.
    KNOWN_MODELS = (
        ModelInfo(
            id="grok-4.3",
            display_name="Grok 4.3",
            context_window=1_000_000,
            tags=("balanced", "flagship"),
        ),
        ModelInfo(
            id="grok-4.20-0309-non-reasoning",
            display_name="Grok 4.20 (non-reasoning)",
            context_window=1_000_000,
            tags=("fast",),
        ),
        ModelInfo(
            id="grok-4.20-0309-reasoning",
            display_name="Grok 4.20 (reasoning)",
            context_window=1_000_000,
            tags=("reasoning",),
        ),
        ModelInfo(
            id="grok-4.20-multi-agent-0309",
            display_name="Grok 4.20 Multi-agent",
            context_window=2_000_000,
            tags=("smart", "long-context"),
        ),
    )
