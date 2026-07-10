"""Session opening — two distinct experiences (§23 + experience overhaul).

SESSION 1: an AI-generated opening built from a BOUNDED context — table profile,
the party's characters WITH their hooks, and the location. It must tie at least
one established character hook into the situation, set a live pressure, and end
on one open decision point. The generator invents fiction from these inputs; it
receives no DM-only records, so it cannot leak any.

SESSION ≥2: deterministic continuity — player-safe recap (retrieval-enforced),
current place & in-world time, mechanically-relevant reminders, and a fresh
decision point. No hardcoded tavern, ever.

Returns kinded OutboundMessages; the adapter renders them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.jobs import SafeRecapGenerator
from app.ai.llm.base import LLMMessage, LLMProvider
from app.ai.prompts.system_prompts import OPENING_SYSTEM
from app.ai.prompts.thai_dm_style import THAI_DM_STYLE
from app.core.clock import format_game_time
from app.core.errors import LLMError
from app.core.ids import SYSTEM_ACTOR
from app.core.logging import get_logger
from app.discord_bridge.dto import OutboundMessage
from app.models.campaign import Campaign, CampaignMember
from app.models.character import Character
from app.models.enums import ActivePlayState, EventType, SceneMode, Visibility
from app.models.location import Location
from app.models.session import Session
from app.presentation import MessageKind
from app.schemas.llm_io import OpeningScene
from app.services.events import EventService
from app.services.scenes import SceneService
from app.services.sessions.session_service import SessionService

log = get_logger(__name__)


@dataclass
class OpeningResult:
    session_id: str
    scene_id: str
    number: int
    messages: list[OutboundMessage] = field(default_factory=list)
    used_hooks: list[str] = field(default_factory=list)
    # Back-compat convenience for callers/tests that want plain text.
    recap_text: str = ""
    opening_text: str = ""
    reminders: list[str] = field(default_factory=list)


class SessionOpeningService:
    def __init__(self, db, provider: LLMProvider) -> None:
        self.db = db
        self.provider = provider
        self.recap = SafeRecapGenerator(provider)

    async def open_new_session(
        self,
        *,
        campaign_id: str,
        attendance_member_ids: list[str],
        location_id: str,
        channel_id: str = "",
        scene_purpose: str = "",
        dramatic_question: str = "",
        participants: list[str] | None = None,
        visible_entity_ids: list[str] | None = None,
        immediate_threat_ids: list[str] | None = None,
        mode: SceneMode = SceneMode.EXPLORATION,
    ) -> OpeningResult:
        # 1. create + open the session.
        async with self.db.unit_of_work() as s:
            session_row = await SessionService(s).create_session(
                campaign_id=campaign_id, attendance=attendance_member_ids
            )
            await SessionService(s).open_session(session_row.id)
            session_id, number = session_row.id, session_row.number

        # 2. gather the bounded opening context (no DM-only records).
        ctx = await self._gather(campaign_id, attendance_member_ids, location_id)
        reminders = ctx["reminders"]

        # 3. build the opening (session 1: generated; later: continuity restore).
        if number == 1:
            opening = await self._generate_first_opening(ctx, scene_purpose)
            recap_text = ""
        else:
            async with self.db.session() as read:
                recap = await self.recap.run(read, campaign_id=campaign_id, session_id=None)
            recap_text = recap.text
            opening = self._continuity_opening(ctx, number)

        # 4. persist scene + lifecycle + events atomically.
        async with self.db.unit_of_work() as s:
            scene = await SceneService(s).create_scene(
                session_id=session_id, location_id=location_id, mode=mode,
                purpose=scene_purpose or opening.title,
                dramatic_question=dramatic_question or opening.decision_prompt,
                participants=participants or ctx["participant_refs"],
                visible_entity_ids=visible_entity_ids or [],
                immediate_threat_ids=immediate_threat_ids or [],
                scene_start_game_time=ctx["game_time"],
            )
            await SessionService(s).begin_active_play(session_id)
            row = await s.get(Session, session_id)
            row.active_play_state = ActivePlayState.TABLE_OPEN.value
            row.version += 1

            events = EventService(s)
            await events.record(
                campaign_id=campaign_id, session_id=session_id,
                event_type=EventType.SESSION_STARTED, actor_entity=SYSTEM_ACTOR,
                visibility=Visibility.PUBLIC,
                payload={"number": number, "summary": f"เริ่มเซสชันที่ {number}: {opening.title}"},
                narrative_significance=20,
            )
            await events.record(
                campaign_id=campaign_id, session_id=session_id, scene_id=scene.id,
                event_type=EventType.SCENE_STARTED, actor_entity=SYSTEM_ACTOR,
                location_id=location_id, visibility=Visibility.PUBLIC,
                payload={"summary": opening.title,
                         "used_hooks": opening.used_hooks},
                narrative_significance=15,
            )
            scene_id = scene.id

        # 5. assemble the kinded message sequence.
        messages: list[OutboundMessage] = [OutboundMessage(
            channel_id, "", kind=MessageKind.SESSION_TITLE,
            title=f"เซสชันที่ {number} — {opening.title}",
            data={"footer": f"{ctx['location_name']} · {format_game_time(ctx['game_time'])}"},
        )]
        if recap_text:
            messages.append(OutboundMessage(
                channel_id, recap_text, kind=MessageKind.PLAYER_SAFE_RECAP,
                title="ความเดิมตอนที่แล้ว",
            ))
        frame_body = "\n".join(opening.situation_lines)
        if opening.pressure:
            frame_body += f"\n\n{opening.pressure}"
        frame_data: dict = {"decision_prompt": opening.decision_prompt or None}
        if reminders:
            frame_data["fields"] = [{
                "name": "เตือนความจำ", "value": "\n".join(f"• {r}" for r in reminders),
                "inline": False,
            }]
        messages.append(OutboundMessage(
            channel_id, frame_body, kind=MessageKind.SCENE_FRAME,
            title=None, data=frame_data,
        ))

        return OpeningResult(
            session_id=session_id, scene_id=scene_id, number=number,
            messages=messages, used_hooks=list(opening.used_hooks),
            recap_text=recap_text,
            opening_text=frame_body, reminders=reminders,
        )

    # --- context gathering (bounded, player-safe inputs only) -------------------
    async def _gather(self, campaign_id, member_ids, location_id) -> dict:
        async with self.db.session() as read:
            campaign = await read.get(Campaign, campaign_id)
            profile = (campaign.config or {}).get("profile", {})
            location = await read.get(Location, location_id)
            chars: list[Character] = []
            reminders: list[str] = []
            for mid in member_ids:
                member = await read.get(CampaignMember, mid)
                if member is None or member.active_character_id is None:
                    continue
                char = await read.get(Character, member.active_character_id)
                if char is None:
                    continue
                chars.append(char)
                if char.hp < char.max_hp:
                    reminders.append(f"{char.name} บาดเจ็บอยู่ (HP {char.hp}/{char.max_hp})")
                for cond in char.conditions or []:
                    reminders.append(f"{char.name} มีสภาวะ: {cond}")
        return {
            "profile": profile,
            "characters": chars,
            "participant_refs": [f"character:{c.id}" for c in chars],
            "location_name": location.name if location else "-",
            "location_desc": location.description_obvious if location else "",
            "game_time": campaign.current_game_time if campaign else 0,
            "reminders": reminders,
        }

    async def _generate_first_opening(self, ctx: dict, scene_purpose: str) -> OpeningScene:
        char_lines = []
        for c in ctx["characters"]:
            hooks = c.hooks or {}
            hook_str = "; ".join(f"{k}={v}" for k, v in hooks.items() if v) or "-"
            char_lines.append(f"- {c.name} ({c.char_class}): {hook_str}")
        profile = ctx["profile"]
        messages: list[LLMMessage] = [
            {"role": "system", "content": THAI_DM_STYLE + "\n" + OPENING_SYSTEM},
            {"role": "user", "content": (
                f"PROFILE: โทน={profile.get('tone', 'ผจญภัยคลาสสิก')}; "
                f"สไตล์={profile.get('balance', 'สมดุล')}\n"
                f"CHARACTERS:\n" + "\n".join(char_lines) + "\n"
                f"LOCATION: {ctx['location_name']} — {ctx['location_desc']}\n"
                f"PURPOSE: {scene_purpose or '-'}"
            )},
        ]
        try:
            return await self.provider.generate_session_opening(messages)
        except LLMError as exc:
            log.warning("opening generator fell back to plain frame: %s", exc)
            names = " และ ".join(c.name for c in ctx["characters"]) or "พวกเจ้า"
            return OpeningScene(
                title="จุดเริ่มต้น",
                situation_lines=[f"{names} อยู่ที่{ctx['location_name']}",
                                 ctx["location_desc"] or "รอบตัวยังเงียบ"],
                pressure="", decision_prompt="จะเริ่มจากตรงไหนดี?",
            )

    def _continuity_opening(self, ctx: dict, number: int) -> OpeningScene:
        return OpeningScene(
            title="เดินทางต่อ",
            situation_lines=[
                f"พวกเจ้ายังอยู่ที่{ctx['location_name']}",
                ctx["location_desc"] or "",
            ],
            pressure="",
            decision_prompt="พร้อมเมื่อไหร่ก็ลุยต่อ — ตอนนี้จะทำอะไร?",
        )
