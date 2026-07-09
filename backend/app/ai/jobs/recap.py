"""SafeRecapGenerator — player-safe recap from player-visible events ONLY.

The recap context is built by `build_recap_context`, which retrieves events through
a visibility-filtered query (PUBLIC/PARTY). DM-only facts are never selected into the
context, so they physically cannot appear in the recap. FALLBACK: minimal event-list.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMProvider
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.memory.context_builders import build_recap_context
from app.models.enums import Visibility
from app.schemas.llm_io import Recap

log = get_logger(__name__)


class SafeRecapGenerator:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def run(
        self, session: AsyncSession, *, campaign_id: str, session_id: str | None,
        allowed_visibilities: list[Visibility] | None = None,
    ) -> Recap:
        messages = await build_recap_context(
            session, campaign_id=campaign_id, session_id=session_id,
            allowed_visibilities=allowed_visibilities,
        )
        try:
            return await self.provider.generate_safe_recap(messages)
        except LLMError as exc:
            log.warning("recap fell back to minimal event list: %s", exc)
            # Minimal recap = the (already visibility-filtered) event lines verbatim.
            body = messages[-1]["content"].replace("EVENTS:", "").strip()
            return Recap(text="เรื่องย่อ:\n" + (body or "ยังไม่มีเหตุการณ์สำคัญ"))
