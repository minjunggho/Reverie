"""OpenAI provider — structured output via a forced function tool.

Not exercised by the test suite. Imported lazily so the `openai` SDK and API key are
only required when this provider is active.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from app.ai.llm._structured import TOOL_NAME, validate_arguments
from app.ai.llm.base import LLMMessage, LLMProvider
from app.core.config import Settings
from app.core.errors import LLMError


class OpenAIProvider(LLMProvider):
    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise LLMError("OPENAI_API_KEY is required for the openai provider")
        import openai  # lazy

        self._client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.llm_model
        self._max_retries = settings.llm_max_retries

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
        function = {
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": "Return the structured result matching the schema.",
                "parameters": response_model.model_json_schema(),
            },
        }
        retries = self._max_retries if max_retries is None else max_retries

        last_error: Exception | None = None
        for _ in range(retries + 1):
            try:
                resp = await self._client.chat.completions.create(
                    model=self._model,
                    temperature=temperature,
                    messages=[{"role": m["role"], "content": m["content"]} for m in messages],
                    tools=[function],
                    tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
                )
            except Exception as exc:  # API/network error -> becomes LLMError so jobs fall back
                last_error = exc
                continue
            choice = resp.choices[0].message
            if choice.tool_calls:
                args = choice.tool_calls[0].function.arguments
                try:
                    return validate_arguments(response_model, json.loads(args))
                except (LLMError, json.JSONDecodeError) as exc:
                    last_error = exc
                    continue
        raise LLMError(f"openai structured_complete failed: {last_error}")
