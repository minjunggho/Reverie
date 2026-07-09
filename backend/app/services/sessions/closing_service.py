"""Minimal session closing (§24) used by the vertical slice.

Supports OWNER_REQUESTED_CLOSE and AI_SUGGESTED_NATURAL_STOP as reasons. Lands the
session in CLOSING, records SESSION_ENDED, and produces a PLAYER-SAFE recap. The full
post-session continuity pipeline (private continuity report) is Phase 10.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.ai.jobs import SafeRecapGenerator
from app.ai.llm.base import LLMProvider
from app.core.ids import SYSTEM_ACTOR
from app.models.enums import EventType, Visibility
from app.services.events import EventService
from app.services.sessions.session_service import SessionService


@dataclass
class ClosingResult:
    session_id: str
    recap_text: str
    reason: str


class SessionClosingService:
    def __init__(self, db, provider: LLMProvider) -> None:
        self.db = db
        self.recap = SafeRecapGenerator(provider)

    async def close_session(
        self, *, campaign_id: str, session_id: str, reason: str = "OWNER_REQUESTED_CLOSE"
    ) -> ClosingResult:
        async with self.db.unit_of_work() as s:
            await SessionService(s).close_session(session_id)  # ACTIVE_PLAY -> CLOSING
            await EventService(s).record(
                campaign_id=campaign_id, session_id=session_id,
                event_type=EventType.SESSION_ENDED, actor_entity=SYSTEM_ACTOR,
                visibility=Visibility.PUBLIC, payload={"reason": reason, "summary": "จบเซสชัน"},
                narrative_significance=20,
            )
        async with self.db.session() as read:
            recap = await self.recap.run(read, campaign_id=campaign_id, session_id=session_id)
        return ClosingResult(session_id=session_id, recap_text=recap.text, reason=reason)
