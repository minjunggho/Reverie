"""CheckSetupGenerator — the fiction-first beat narrated BEFORE the dice prompt
(issue #1, item D).

FORBIDDEN: revealing the outcome, implying success/failure, revealing DC, committing
any state, or rolling. FALLBACK: a terse, outcome-neutral line naming the check.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMProvider
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.memory.context_builders import build_check_setup_context
from app.schemas.llm_io import CheckSetup

log = get_logger(__name__)


class CheckSetupGenerator:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def run(
        self, session: AsyncSession, *, action_text: str, check_label: str,
        scene=None, directory=None, scene_context=None, character_context=None,
        pacing=None,
    ) -> CheckSetup:
        messages = await build_check_setup_context(
            session, action_text=action_text, check_label=check_label,
            scene=scene, directory=directory, scene_context=scene_context,
            character_context=character_context, pacing=pacing,
        )
        try:
            return await self.provider.generate_check_setup(messages)
        except LLMError as exc:
            log.warning("check setup fell back to a terse neutral line: %s", exc)
            return CheckSetup(text=f"{action_text} — ทุกอย่างแขวนอยู่บนความไม่แน่นอน")
