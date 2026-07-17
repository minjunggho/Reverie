"""DMNarrator — committed result + narration context -> Thai narration.

FORBIDDEN: changing any number/outcome; adding consequences; revealing hidden info
(structurally impossible — restricted facts are filtered before the context is built).
FALLBACK: terse factual Thai narration of the committed result.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMProvider
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.memory.context_builders import build_narration_context
from app.models.scene import Scene
from app.schemas.llm_io import Narration

log = get_logger(__name__)


class DMNarrator:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def run(
        self, session: AsyncSession, *, action_text: str, outcome: str,
        result_summary: str, scene: Scene | None, target_ref: str | None = None,
        directory=None, resolved_targets=None, scene_context=None, pacing=None,
        consequence_class=None, narration_hint: str = "", character_context=None,
        progression_context=None, stall_state=None,
    ) -> Narration:
        messages = await build_narration_context(
            session, action_text=action_text, outcome=outcome,
            result_summary=result_summary, scene=scene, target_ref=target_ref,
            directory=directory, resolved_targets=resolved_targets,
            scene_context=scene_context, pacing=pacing,
            consequence_class=consequence_class, narration_hint=narration_hint,
            character_context=character_context,
            progression_context=progression_context,
            stall_state=stall_state,
        )
        try:
            return await self.provider.generate_dm_narration(messages)
        except LLMError as exc:
            log.warning("narrator fell back to terse factual narration: %s", exc)
            verb = "สำเร็จ" if outcome == "success" else "ล้มเหลว"
            return Narration(text=f"{verb} — {result_summary}", style="concise")
