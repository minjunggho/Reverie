"""Session closing — a deliberate ending, not a status change (§24 + overhaul).

Sequence: closing beat (one quiet Thai line-pair grounded in the last events) →
SESSION_END chronicle (decisions, discoveries, items, objectives, open questions —
all derived from player-visible canonical events) → one-tap optional feedback.

The chronicle is assembled DETERMINISTICALLY from events; the AI supplies only the
narrative recap paragraph via the (already retrieval-safe) SafeRecapGenerator.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.jobs import SafeRecapGenerator
from app.ai.llm.base import LLMProvider
from app.core.ids import SYSTEM_ACTOR
from app.discord_bridge.dto import BridgeResult, OutboundMessage
from app.models.campaign import Campaign
from app.models.enums import EventType, Visibility
from app.models.session import Session
from app.presentation import MessageKind
from app.services.events import EventService
from app.services.sessions.session_service import SessionService

FEEDBACK_CHOICES = ["🔥 สนุกมาก", "🙂 ก็ดีนะ", "😴 วันนี้เหนื่อยๆ"]


@dataclass
class ClosingResult:
    session_id: str
    recap_text: str
    reason: str
    messages: list[OutboundMessage] = field(default_factory=list)


class SessionClosingService:
    def __init__(self, db, provider: LLMProvider) -> None:
        self.db = db
        self.recap = SafeRecapGenerator(provider)

    async def close_session(
        self, *, campaign_id: str, session_id: str,
        reason: str = "OWNER_REQUESTED_CLOSE", channel_id: str = "",
    ) -> ClosingResult:
        async with self.db.unit_of_work() as s:
            await SessionService(s).close_session(session_id)  # ACTIVE_PLAY -> CLOSING
            await EventService(s).record(
                campaign_id=campaign_id, session_id=session_id,
                event_type=EventType.SESSION_ENDED, actor_entity=SYSTEM_ACTOR,
                visibility=Visibility.PUBLIC, payload={"reason": reason, "summary": "จบเซสชัน"},
                narrative_significance=20,
            )
            # Arm the light feedback catcher (consumed by the bridge).
            campaign = await s.get(Campaign, campaign_id)
            config = dict(campaign.config or {})
            config["awaiting_feedback_session"] = session_id
            campaign.config = config

        # Narrative recap (player-visible events only) + structured chronicle.
        async with self.db.session() as read:
            recap = await self.recap.run(read, campaign_id=campaign_id, session_id=session_id)
            events = await EventService(read).list_visible_events(
                campaign_id=campaign_id, session_id=session_id,
                allowed_visibilities=[Visibility.PUBLIC, Visibility.PARTY],
            )

        def _summaries(*types: EventType) -> list[str]:
            wanted = {t.value for t in types}
            return [e.payload.get("summary", "") for e in events
                    if e.event_type in wanted and isinstance(e.payload, dict)
                    and e.payload.get("summary")]

        decisions = _summaries(EventType.PLAYER_ACTION_COMMITTED)
        discoveries = _summaries(EventType.KNOWLEDGE_GAINED)
        items = _summaries(EventType.ITEM_GAINED, EventType.ITEM_LOST)
        objectives = _summaries(EventType.QUEST_STATE_CHANGED)

        fields = []
        if decisions:
            fields.append({"name": "การตัดสินใจสำคัญ",
                           "value": "\n".join(f"• {d}" for d in decisions[-5:]), "inline": False})
        if discoveries:
            fields.append({"name": "สิ่งที่ค้นพบ",
                           "value": "\n".join(f"• {d}" for d in discoveries[-5:]), "inline": False})
        if items:
            fields.append({"name": "ของที่ได้มา/เสียไป",
                           "value": "\n".join(f"• {i}" for i in items[-5:]), "inline": False})
        fields.append({"name": "เป้าหมายที่ยังค้าง",
                       "value": "\n".join(f"• {o}" for o in objectives[-3:]) or "• เรื่องราวยังเปิดอยู่",
                       "inline": False})

        beat = OutboundMessage(
            channel_id,
            "แสงไฟเริ่มหรี่ลง เรื่องราวคืนนี้พักไว้ตรงนี้ก่อน\nแล้วพบกันใหม่ในการเดินทางครั้งหน้า",
            kind=MessageKind.SCENE_TRANSITION,
        )
        chronicle = OutboundMessage(
            channel_id, recap.text, kind=MessageKind.SESSION_END,
            title="บันทึกการผจญภัยคืนนี้",
            data={"fields": fields, "footer": "อ่านย้อนหลังได้ทุกเมื่อ: !rv journal"},
        )
        feedback = OutboundMessage(
            channel_id, "คืนนี้เป็นไงบ้าง? (แตะได้ ไม่ตอบก็ได้)",
            kind=MessageKind.TABLE_NOTICE, choices=list(FEEDBACK_CHOICES),
        )
        return ClosingResult(
            session_id=session_id, recap_text=recap.text, reason=reason,
            messages=[beat, chronicle, feedback],
        )

    @staticmethod
    async def try_record_feedback(db, *, campaign: Campaign, member_id: str, text: str) -> bool:
        """If the campaign is awaiting feedback and this text is one of the choices,
        record it on the session and disarm. Returns True when consumed."""
        session_id = (campaign.config or {}).get("awaiting_feedback_session")
        if not session_id or text.strip() not in FEEDBACK_CHOICES:
            return False
        async with db.unit_of_work() as s:
            row = await s.get(Session, session_id)
            if row is not None:
                fb = dict(row.feedback or {})
                fb[member_id] = text.strip()
                row.feedback = fb
            camp = await s.get(Campaign, campaign.id)
            config = dict(camp.config or {})
            config.pop("awaiting_feedback_session", None)
            camp.config = config
        return True
