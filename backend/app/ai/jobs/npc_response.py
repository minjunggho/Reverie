"""NPCResponseGenerator — NPC-scoped context -> dialogue + proposed belief/attitude
deltas.

FORBIDDEN: using facts outside the NPC's knowledge (structurally prevented by the
retrieval boundary); committing deltas directly. FALLBACK: cautious in-character
non-answer.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMProvider
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.memory.context_builders import build_npc_response_context
from app.schemas.llm_io import NPCResponse

log = get_logger(__name__)


class NPCResponseGenerator:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def run(
        self, session: AsyncSession, *, npc, listener_ref: str, utterance: str,
        listener_name: str | None = None, game_time: int = 0,
        decision_block: str | None = None,
    ) -> NPCResponse:
        messages = await build_npc_response_context(
            session, npc=npc, listener_ref=listener_ref, utterance=utterance,
            listener_name=listener_name, game_time=game_time,
            decision_block=decision_block,
        )
        try:
            return await self.provider.generate_npc_response(messages)
        except LLMError as exc:
            log.warning("npc response fell back to cautious non-answer: %s", exc)
            return NPCResponse(utterance="...ข้าไม่แน่ใจว่าเจ้าหมายความว่าอะไร")
