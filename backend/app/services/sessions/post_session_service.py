"""Post-session pipeline (§25).

Produces two artifacts:
- a PLAYER-SAFE summary (from visible events, via PostSessionAnalyzer), and
- a PRIVATE continuity report assembled DETERMINISTICALLY from canonical events +
  state (never from prose).

Then advances the session CLOSING -> POST_SESSION -> COMPLETE. Next-session prep
consumes the continuity report + canonical DB state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ai.jobs.post_session import PostSessionAnalyzer
from app.ai.llm.base import LLMProvider
from app.models.campaign import Campaign
from app.models.enums import EventType, SessionStatus, Visibility
from app.services.events import EventService
from app.services.sessions.session_service import SessionService


@dataclass
class PostSessionArtifacts:
    session_id: str
    player_summary: str
    continuity_report: dict[str, Any] = field(default_factory=dict)


class PostSessionService:
    def __init__(self, db, provider: LLMProvider) -> None:
        self.db = db
        self.analyzer = PostSessionAnalyzer(provider)

    async def run(self, *, campaign_id: str, session_id: str) -> PostSessionArtifacts:
        # 1. player-safe narrative summary (visible events only).
        async with self.db.session() as read:
            player_summary = await self.analyzer.player_summary(
                read, campaign_id=campaign_id, session_id=session_id
            )
        # 2. private continuity report from canonical events + state.
        continuity = await self._build_continuity(campaign_id, session_id)
        # 3. advance lifecycle CLOSING -> POST_SESSION -> COMPLETE.
        async with self.db.unit_of_work() as s:
            await SessionService(s).transition_status(session_id, SessionStatus.POST_SESSION)
            await SessionService(s).transition_status(session_id, SessionStatus.COMPLETE)
        return PostSessionArtifacts(
            session_id=session_id, player_summary=player_summary, continuity_report=continuity
        )

    async def _build_continuity(self, campaign_id: str, session_id: str) -> dict[str, Any]:
        async with self.db.session() as read:
            events = await EventService(read).list_events(
                campaign_id=campaign_id, session_id=session_id
            )
            campaign = await read.get(Campaign, campaign_id)

        def _by(kind: EventType) -> list[dict]:
            return [
                {"seq": e.seq, "payload": e.payload, "mechanical_changes": e.mechanical_changes,
                 "visibility": e.visibility}
                for e in events if e.event_type == kind.value
            ]

        return {
            "current_campaign_time": campaign.current_game_time if campaign else 0,
            "canonical_event_count": len(events),
            "npc_state_changes": _by(EventType.NPC_STATE_CHANGED),
            "world_time_advances": _by(EventType.WORLD_TIME_ADVANCED),
            "threat_progress": _by(EventType.THREAT_ADVANCED),
            "quest_changes": _by(EventType.QUEST_STATE_CHANGED),
            "knowledge_gained": _by(EventType.KNOWLEDGE_GAINED),
            # DM-scoped developments are legitimately IN the private report.
            "secret_developments": [
                {"seq": e.seq, "payload": e.payload}
                for e in events if e.visibility == Visibility.DM_ONLY.value
            ],
            "unresolved_threads": [],  # scaffolded; populated as quests/threads mature
        }
