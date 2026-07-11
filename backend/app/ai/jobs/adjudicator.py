"""AdjudicationJudge — interpretation + context -> resolution type + ability/skill +
proposed DC band + clarification flag.

FORBIDDEN: computing modifiers, rolling, committing. FALLBACK: ABILITY_CHECK at Medium.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMProvider
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.memory.context_builders import build_adjudication_context
from app.models.character import Character
from app.models.enums import DifficultyBand, ResolutionType
from app.models.scene import Scene
from app.schemas.llm_io import ActionInterpretation, AdjudicationDecision

log = get_logger(__name__)


class AdjudicationJudge:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def run(
        self, session: AsyncSession, *, action_text: str,
        interpretation: ActionInterpretation, scene: Scene | None,
        character: Character | None, directory=None, resolved_targets=None,
    ) -> AdjudicationDecision:
        summary = (
            f"goal={interpretation.goal}; method={interpretation.method}; "
            f"constraints={interpretation.declared_constraints}; "
            f"missing={interpretation.missing_information}; "
            f"confidence={interpretation.intent_confidence:.2f}"
        )
        messages = await build_adjudication_context(
            session, action_text=action_text, interpretation_summary=summary,
            scene=scene, character=character, directory=directory,
            resolved_targets=resolved_targets,
        )
        try:
            return await self.provider.adjudicate_uncertain_action(messages)
        except LLMError as exc:
            log.warning("adjudicator fell back to Medium ability check: %s", exc)
            return AdjudicationDecision(
                resolution_type=ResolutionType.ABILITY_CHECK, ability="wis", skill="perception",
                dc_band=DifficultyBand.MEDIUM, rationale="fallback: safe default",
            )
