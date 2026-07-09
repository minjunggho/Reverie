"""Select the active LLMProvider from settings.

Default is `fake` so the engine boots and all tests run with no credentials.
"""
from __future__ import annotations

from app.ai.llm.base import LLMProvider
from app.ai.llm.fake import FakeLLMProvider
from app.core.config import Settings, get_settings


def get_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    provider = (settings.llm_provider or "fake").lower()

    if provider == "fake":
        return FakeLLMProvider()
    if provider == "anthropic":
        from app.ai.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(settings)
    if provider == "openai":
        from app.ai.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(settings)
    raise ValueError(f"unknown LLM provider: {settings.llm_provider!r}")
