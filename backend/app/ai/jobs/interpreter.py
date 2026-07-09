"""ActionInterpreter — a `!` action's Thai text -> structured intent.

FORBIDDEN: mechanics selection, state mutation, dice. FALLBACK: low confidence with
missing_information, which drives the clarification model.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMProvider
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.memory.context_builders import build_action_interpretation_context
from app.models.character import Character
from app.models.scene import Scene
from app.schemas.llm_io import ActionInterpretation

log = get_logger(__name__)


class ActionInterpreter:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def run(
        self, session: AsyncSession, *, action_text: str,
        scene: Scene | None, character: Character | None,
    ) -> ActionInterpretation:
        messages = await build_action_interpretation_context(
            session, action_text=action_text, scene=scene, character=character
        )
        try:
            return await self.provider.interpret_committed_action(messages)
        except LLMError as exc:
            log.warning("interpreter fell back to clarification: %s", exc)
            return ActionInterpretation(
                goal=action_text[:60] or "ไม่ทราบ", method="ไม่ทราบ",
                intent_confidence=0.0,
                missing_information=["ตีความเจตนาไม่ได้ ขอรายละเอียดเพิ่ม"],
            )
