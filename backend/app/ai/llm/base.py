"""The LLMProvider abstraction.

`structured_complete` is the single primitive: given a Pydantic `response_model` and
a list of messages, it returns a validated instance of that model, retrying on
invalid output up to a bound. The task-oriented convenience methods are thin typed
wrappers over it (they fix the schema and a `task` label used for routing/telemetry
and by the FakeLLMProvider). No convenience method mutates state or rolls dice.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypedDict, TypeVar

from pydantic import BaseModel

from app.schemas.llm_io import (
    ActionInterpretation,
    AdjudicationDecision,
    ClassificationResult,
    ConsequenceProposal,
    Narration,
    NPCResponse,
    PostSessionReport,
    Recap,
)

T = TypeVar("T", bound=BaseModel)


class LLMMessage(TypedDict):
    role: str  # "system" | "user" | "assistant"
    content: str


class LLMProvider(ABC):
    """All providers implement `structured_complete`; the rest is shared."""

    @abstractmethod
    async def structured_complete(
        self,
        *,
        response_model: type[T],
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        task: str | None = None,
        temperature: float = 0.7,
        max_retries: int | None = None,
    ) -> T:
        """Return a validated `response_model` instance. Raises `LLMError` if the
        model cannot produce valid output within the retry bound."""

    # --- Task-oriented convenience methods (schema-fixed wrappers) -----------

    async def classify_table_message(
        self, messages: list[LLMMessage]
    ) -> ClassificationResult:
        return await self.structured_complete(
            response_model=ClassificationResult, messages=messages,
            task="classify_table_message", temperature=0.0,
        )

    async def interpret_committed_action(
        self, messages: list[LLMMessage]
    ) -> ActionInterpretation:
        return await self.structured_complete(
            response_model=ActionInterpretation, messages=messages,
            task="interpret_committed_action", temperature=0.0,
        )

    async def adjudicate_uncertain_action(
        self, messages: list[LLMMessage]
    ) -> AdjudicationDecision:
        return await self.structured_complete(
            response_model=AdjudicationDecision, messages=messages,
            task="adjudicate_uncertain_action", temperature=0.0,
        )

    async def plan_consequence(
        self, messages: list[LLMMessage]
    ) -> ConsequenceProposal:
        return await self.structured_complete(
            response_model=ConsequenceProposal, messages=messages,
            task="plan_consequence", temperature=0.2,
        )

    async def generate_dm_narration(self, messages: list[LLMMessage]) -> Narration:
        return await self.structured_complete(
            response_model=Narration, messages=messages,
            task="generate_dm_narration", temperature=0.7,
        )

    async def generate_npc_response(self, messages: list[LLMMessage]) -> NPCResponse:
        return await self.structured_complete(
            response_model=NPCResponse, messages=messages,
            task="generate_npc_response", temperature=0.6,
        )

    async def generate_safe_recap(self, messages: list[LLMMessage]) -> Recap:
        return await self.structured_complete(
            response_model=Recap, messages=messages,
            task="generate_safe_recap", temperature=0.5,
        )

    async def process_post_session_continuity(
        self, messages: list[LLMMessage]
    ) -> PostSessionReport:
        return await self.structured_complete(
            response_model=PostSessionReport, messages=messages,
            task="process_post_session_continuity", temperature=0.3,
        )
