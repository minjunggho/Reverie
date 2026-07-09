"""FakeLLMProvider — deterministic, scripted, schema-valid responses for tests.

Never makes a network call. A test wires responses per `task` in one of three ways
(checked in this order):

1. `push(task, value)`   — FIFO queue of responses for repeated calls.
2. `on(task, handler)`   — a callable `(messages, response_model) -> value`.
3. `default_handler`     — a global fallback callable, or None.

`value` may be a model instance, a dict, or a callable; it is coerced/validated to
`response_model`. `simulate_invalid(task, n)` makes the first `n` calls return
invalid data so retry/fallback logic can be tested. Every call is recorded in
`self.calls` for assertions.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Callable

from pydantic import BaseModel, ValidationError as PydanticValidationError

from app.ai.llm.base import LLMMessage, LLMProvider
from app.core.errors import LLMError

Handler = Callable[[list[LLMMessage], type[BaseModel]], Any]


class FakeLLMProvider(LLMProvider):
    def __init__(self, default_handler: Handler | None = None) -> None:
        self._queues: dict[str, deque] = defaultdict(deque)
        self._handlers: dict[str, Handler] = {}
        self._invalid_counts: dict[str, int] = defaultdict(int)
        self.default_handler: Handler | None = default_handler
        self.calls: list[tuple[str | None, list[LLMMessage]]] = []

    # --- scripting API -------------------------------------------------------
    def push(self, task: str, *values: Any) -> "FakeLLMProvider":
        self._queues[task].extend(values)
        return self

    def on(self, task: str, handler: Handler) -> "FakeLLMProvider":
        self._handlers[task] = handler
        return self

    def simulate_invalid(self, task: str, times: int = 1) -> "FakeLLMProvider":
        self._invalid_counts[task] += times
        return self

    # --- provider primitive --------------------------------------------------
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
        self.calls.append((task, messages))
        retries = 2 if max_retries is None else max_retries

        last_error: Exception | None = None
        for _ in range(retries + 1):
            if task is not None and self._invalid_counts.get(task, 0) > 0:
                self._invalid_counts[task] -= 1
                last_error = PydanticValidationError.from_exception_data(
                    response_model.__name__, []
                )
                continue
            raw = self._resolve(task, messages, response_model)
            try:
                return self._coerce(raw, response_model, messages)
            except PydanticValidationError as exc:  # pragma: no cover - defensive
                last_error = exc
                continue

        raise LLMError(
            f"FakeLLMProvider could not produce valid {response_model.__name__} "
            f"for task={task!r}: {last_error}"
        )

    # --- internals -----------------------------------------------------------
    def _resolve(self, task: str | None, messages, response_model) -> Any:
        if task is not None and self._queues.get(task):
            return self._queues[task].popleft()
        if task is not None and task in self._handlers:
            return self._handlers[task](messages, response_model)
        if self.default_handler is not None:
            return self.default_handler(messages, response_model)
        raise LLMError(
            f"FakeLLMProvider has no scripted response for task={task!r} "
            f"({response_model.__name__}). Use push()/on()/default_handler."
        )

    @staticmethod
    def _coerce(raw: Any, response_model: type[BaseModel], messages) -> BaseModel:
        if callable(raw) and not isinstance(raw, BaseModel):
            raw = raw(messages, response_model)
        if isinstance(raw, response_model):
            return raw
        if isinstance(raw, BaseModel):
            return response_model.model_validate(raw.model_dump())
        if isinstance(raw, dict):
            return response_model.model_validate(raw)
        raise LLMError(f"cannot coerce {type(raw)!r} to {response_model.__name__}")
