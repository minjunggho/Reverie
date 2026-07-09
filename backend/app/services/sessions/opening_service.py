"""Session-opening orchestration (§23).

Determines attendance -> restores state -> generates a PLAYER-SAFE recap (DM-only
info is filtered at retrieval) -> surfaces only mechanically-relevant character
reminders -> restores the location/scene -> frames the opening -> leaves the table
at rest (TABLE_OPEN) with a clear decision point.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.jobs import SafeRecapGenerator
from app.ai.llm.base import LLMProvider
from app.core.ids import SYSTEM_ACTOR
from app.models.campaign import CampaignMember
from app.models.character import Character
from app.models.enums import ActivePlayState, EventType, SceneMode, Visibility
from app.models.location import Location
from app.models.session import Session
from app.services.events import EventService
from app.services.scenes import SceneService
from app.services.sessions.session_service import SessionService


@dataclass
class OpeningResult:
    session_id: str
    scene_id: str
    number: int
    recap_text: str
    opening_text: str
    reminders: list[str] = field(default_factory=list)


class SessionOpeningService:
    def __init__(self, db, provider: LLMProvider) -> None:
        self.db = db
        self.recap = SafeRecapGenerator(provider)

    async def open_new_session(
        self,
        *,
        campaign_id: str,
        attendance_member_ids: list[str],
        location_id: str,
        scene_purpose: str = "",
        dramatic_question: str = "",
        participants: list[str] | None = None,
        visible_entity_ids: list[str] | None = None,
        immediate_threat_ids: list[str] | None = None,
        mode: SceneMode = SceneMode.EXPLORATION,
    ) -> OpeningResult:
        # 1-2. create + open the session.
        async with self.db.unit_of_work() as s:
            session_row = await SessionService(s).create_session(
                campaign_id=campaign_id, attendance=attendance_member_ids
            )
            await SessionService(s).open_session(session_row.id)  # PREPARATION -> OPENING
            session_id, number = session_row.id, session_row.number

        # 3. player-safe recap from prior VISIBLE campaign history (none for session 1).
        async with self.db.session() as read:
            recap = await self.recap.run(read, campaign_id=campaign_id, session_id=None)

        # 4. mechanically-relevant reminders only.
        reminders = await self._build_reminders(attendance_member_ids)

        # 5. restore location/scene, begin active play, record events (atomic).
        async with self.db.unit_of_work() as s:
            scene = await SceneService(s).create_scene(
                session_id=session_id, location_id=location_id, mode=mode,
                purpose=scene_purpose, dramatic_question=dramatic_question,
                participants=participants or [], visible_entity_ids=visible_entity_ids or [],
                immediate_threat_ids=immediate_threat_ids or [],
            )
            await SessionService(s).begin_active_play(session_id)  # OPENING -> ACTIVE_PLAY (framing)
            session_row = await s.get(Session, session_id)
            session_row.active_play_state = ActivePlayState.TABLE_OPEN.value  # framing done -> rest
            session_row.version += 1

            events = EventService(s)
            await events.record(
                campaign_id=campaign_id, session_id=session_id, event_type=EventType.SESSION_STARTED,
                actor_entity=SYSTEM_ACTOR, visibility=Visibility.PUBLIC,
                payload={"number": number, "summary": f"เริ่มเซสชันที่ {number}"},
                narrative_significance=20,
            )
            await events.record(
                campaign_id=campaign_id, session_id=session_id, scene_id=scene.id,
                event_type=EventType.SCENE_STARTED, actor_entity=SYSTEM_ACTOR,
                location_id=location_id, visibility=Visibility.PUBLIC,
                payload={"summary": scene_purpose or "เปิดฉาก"}, narrative_significance=15,
            )
            scene_id = scene.id

        opening_text = await self._frame_opening(location_id, scene_purpose)
        return OpeningResult(
            session_id=session_id, scene_id=scene_id, number=number,
            recap_text=recap.text, opening_text=opening_text, reminders=reminders,
        )

    # --- helpers -------------------------------------------------------------
    async def _build_reminders(self, member_ids: list[str]) -> list[str]:
        reminders: list[str] = []
        async with self.db.session() as read:
            for mid in member_ids:
                member = await read.get(CampaignMember, mid)
                if member is None or member.active_character_id is None:
                    continue
                char = await read.get(Character, member.active_character_id)
                if char is None:
                    continue
                if char.hp < char.max_hp:
                    reminders.append(f"{char.name} บาดเจ็บอยู่ (HP {char.hp}/{char.max_hp})")
                for cond in char.conditions or []:
                    reminders.append(f"{char.name} มีสภาวะ: {cond}")
        return reminders

    async def _frame_opening(self, location_id: str, scene_purpose: str) -> str:
        async with self.db.session() as read:
            loc = await read.get(Location, location_id)
        parts = []
        if loc is not None:
            parts.append(loc.name)
            if loc.description_obvious:
                parts.append(loc.description_obvious)
        if scene_purpose:
            parts.append(scene_purpose)
        return "\n".join(parts) if parts else "ฉากเปิดเริ่มขึ้น"
