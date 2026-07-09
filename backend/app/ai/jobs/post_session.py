"""PostSessionAnalyzer — canonical events -> player-safe narrative summary.

The player summary is generated from VISIBLE events only (retrieval-filtered), so it
cannot leak DM-only facts. The structured PRIVATE continuity report is NOT produced
here from prose — it is assembled deterministically from canonical events + state by
PostSessionService (prose is never treated as the database). FALLBACK: template.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMProvider
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.memory.context_builders import build_recap_context

log = get_logger(__name__)


class PostSessionAnalyzer:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def player_summary(
        self, session: AsyncSession, *, campaign_id: str, session_id: str
    ) -> str:
        messages = await build_recap_context(
            session, campaign_id=campaign_id, session_id=session_id
        )
        try:
            report = await self.provider.process_post_session_continuity(messages)
            return report.player_summary
        except LLMError as exc:
            log.warning("post-session analyzer fell back to template summary: %s", exc)
            body = messages[-1]["content"].replace("EVENTS:", "").strip()
            return "สรุปเซสชัน:\n" + (body or "ไม่มีเหตุการณ์สำคัญ")
