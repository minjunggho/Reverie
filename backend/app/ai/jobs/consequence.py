"""ConsequencePlanner — committed mechanical outcome -> consequence class + proposed
deltas.

FORBIDDEN: changing any number/outcome the engine already decided; adding
consequences that bypass validation. FALLBACK: plain SUCCESS/FAILURE, no deltas.
The engine validates and commits every delta through DeltaApplier.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMProvider
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.memory.context_builders import build_consequence_context
from app.models.enums import ConsequenceClass
from app.models.scene import Scene
from app.schemas.llm_io import ConsequenceProposal

log = get_logger(__name__)


class ConsequencePlanner:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def run(
        self, session: AsyncSession, *, action_text: str, outcome: str,
        scene: Scene | None, target_ref: str | None,
    ) -> ConsequenceProposal:
        messages = await build_consequence_context(
            session, action_text=action_text, outcome=outcome, scene=scene, target_ref=target_ref
        )
        try:
            return await self.provider.plan_consequence(messages)
        except LLMError as exc:
            log.warning("consequence planner fell back to plain outcome: %s", exc)
            cls = ConsequenceClass.SUCCESS if outcome == "success" else ConsequenceClass.FAILURE
            return ConsequenceProposal(consequence_class=cls, deltas=[])
