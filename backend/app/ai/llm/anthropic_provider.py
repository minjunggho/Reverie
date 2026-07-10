"""Anthropic provider — structured output via forced tool use.

Not exercised by the test suite (tests use FakeLLMProvider). Imported lazily so the
`anthropic` SDK and API key are only required when this provider is actually active.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from app.ai.llm._structured import TOOL_NAME, tool_schema, validate_arguments
from app.ai.llm.base import LLMMessage, LLMProvider
from app.core.config import Settings
from app.core.errors import LLMError


class AnthropicProvider(LLMProvider):
    def __init__(self, settings: Settings) -> None:
        if not settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is required for the anthropic provider")
        import anthropic  # lazy

        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.llm_model
        self._max_retries = settings.llm_max_retries
        self._timeout = settings.llm_timeout_seconds

    async def structured_complete(
        self,
        *,
        response_model: type[BaseModel],
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        task: str | None = None,
        temperature: float = 0.7,
        max_retries: int | None = None,
    ) -> BaseModel:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        chat = [m for m in messages if m["role"] != "system"]
        tool = tool_schema(response_model)
        retries = self._max_retries if max_retries is None else max_retries

        last_error: Exception | None = None
        for _ in range(retries + 1):
            try:
                resp = await self._client.messages.create(
                    model=self._model,
                    max_tokens=2048,
                    temperature=temperature,
                    system=system or None,
                    messages=[{"role": m["role"], "content": m["content"]} for m in chat],
                    tools=[tool],
                    tool_choice={"type": "tool", "name": TOOL_NAME},
                    timeout=self._timeout,
                )
            except Exception as exc:  # API/network error -> LLMError so jobs fall back
                last_error = exc
                continue
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
                    try:
                        return validate_arguments(response_model, block.input)
                    except (LLMError, ValidationError) as exc:
                        last_error = exc
                        break
        raise LLMError(f"anthropic structured_complete failed: {last_error}")
