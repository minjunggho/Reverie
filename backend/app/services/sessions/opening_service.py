"""Create a session and deliver one grounded, cinematic scene.

This service is the only live ``!rv session start`` narrator.  It does not produce a
campaign prologue, synopsis cards, or a world-to-party briefing.  It first persists the
authoritative session/scene/positions/shared DecisionWindow, then projects that state
into a bounded :class:`ScenePacket`, and finally asks the model to render one connected
Thai scene.  Model failure uses the same packet to produce a deterministic real scene.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.llm.base import LLMMessage, LLMProvider
from app.ai.prompts.system_prompts import CAMPAIGN_OPENING_SYSTEM, OPENING_SYSTEM
from app.ai.prompts.thai_narration_templates import narration_template
from app.ai.prompts.thai_dm_style import THAI_DM_STYLE
from app.core.errors import LLMError
from app.core.ids import SYSTEM_ACTOR
from app.core.logging import get_logger
from app.discord_bridge.dto import OutboundMessage
from app.models.campaign import Campaign, CampaignMember
from app.models.character import Character
from app.models.decision_window import DecisionWindow
from app.models.enums import ActivePlayState, EventType, SceneMode, Visibility
from app.models.location import Location
from app.models.scene import Scene
from app.models.session import Session
from app.memory.scene_packet import ScenePacket, ScenePacketBuilder
from app.presentation import MessageKind
from app.presentation.screens import cinematic_scene_screen
from app.rounds import DecisionWindowService, WindowPolicies
from app.models.enums import WindowMode
from app.schemas.llm_io import OpeningScene
from app.services.events import EventService
from app.services.scenes import SceneService
from app.services.sessions.session_service import SessionService

log = get_logger(__name__)

# Placeholder purpose used only when a campaign gives the opening no explicit intent.
# It is a scaffolding value, never player-facing prose, so the deterministic fallback
# scene filters it out instead of narrating "พวกคุณมาที่นี่เพราะเปิดฉาก...".
_DEFAULT_SCENE_PURPOSE = "เปิดฉากและส่งการตัดสินใจให้ปาร์ตี้"


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

    async def resolve_opening_location(
        self, *, campaign_id: str, attendance_member_ids: list[str]
    ) -> str | None:
        """Canonical WHERE-to-open resolution (E7). Creation order is NEVER intent:
        1. current party anchor (continuity — where play last was)
        2. attending characters' canonical position (majority)
        — an ONGOING campaign may resolve ONLY from the two continuity sources above;
          if both are missing/dangling this raises StateIntegrityError instead of
          silently teleporting the party back to the campaign start —
        3. campaign.starting_location_id (imported / AI-approved / owner-set)
        4. legacy session_prep.opening_location_id
        5. the campaign's ONLY location (unambiguous by count, never 'latest')
        3–5 are legal only for a campaign with NO prior sessions (a genuine beginning).
        None → the caller must show a setup-incomplete notice; nothing is invented."""
        from collections import Counter

        from sqlalchemy import func, select

        from app.core.errors import StateIntegrityError
        from app.models.location import Location
        from app.world import LocationService

        async with self.db.session() as s:
            campaign = await s.get(Campaign, campaign_id)
            if campaign is None:
                return None

            async def _alive(location_id: str | None) -> str | None:
                if not location_id:
                    return None
                loc = await s.get(Location, location_id)
                return loc.id if loc is not None and loc.campaign_id == campaign_id else None

            anchor = await _alive(campaign.current_party_anchor_id)
            if anchor:
                return anchor
            positions: list[str] = []
            for mid in attendance_member_ids:
                member = await s.get(CampaignMember, mid)
                if member is None or member.active_character_id is None:
                    continue
                char = await s.get(Character, member.active_character_id)
                if char is not None and char.location_id:
                    positions.append(char.location_id)
            for candidate, _ in Counter(positions).most_common():
                found = await _alive(candidate)
                if found:
                    return found

            # Continuity sources exhausted. If this campaign has EVER had a session,
            # falling back to the campaign start would teleport the party to the
            # opening — the exact `saved_location or campaign.start_location` failure.
            # Stop, preserve state, and report what is broken instead.
            prior_sessions = (await s.execute(
                select(func.count(Session.id)).where(Session.campaign_id == campaign_id)
            )).scalar_one()
            if prior_sessions > 0:
                raise StateIntegrityError(
                    f"campaign {campaign_id} has {prior_sessions} prior session(s) but no "
                    f"valid party position: anchor="
                    f"{campaign.current_party_anchor_id!r} (missing or dangling), "
                    f"attending character positions={positions!r} (none resolvable). "
                    "Refusing to fall back to the campaign start — repair explicitly "
                    "(owner: `!rv session start at <location>`) or restore the data."
                )

            start = await _alive(campaign.starting_location_id)
            if start:
                return start
            legacy = await _alive((campaign.session_prep or {}).get("opening_location_id"))
            if legacy:
                return legacy
            only = await LocationService(s).only_location(campaign_id)
            return only.id if only is not None else None

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
        # 0. resolve WHERE to open when the caller didn't pin it (canonical priority;
        #    never an invented default).
        if not location_id:
            location_id = await self.resolve_opening_location(
                campaign_id=campaign_id, attendance_member_ids=attendance_member_ids)
            if not location_id:
                from app.core.errors import ValidationError

                raise ValidationError(
                    "campaign has no starting location — import a world, create one "
                    "with AI, or pick a location explicitly")

        # Imported Session Prep constrains WHAT is happening; location resolution
        # above owns WHERE.  Existing campaigns need no data rewrite: absent v2 keys
        # simply project as empty fields in ScenePacket.
        async with self.db.session() as read:
            campaign = await read.get(Campaign, campaign_id)
            prep = dict((campaign.session_prep if campaign else None) or {})
            campaign_config = dict((campaign.config if campaign else None) or {})
            profile = dict(campaign_config.get("profile") or {})

        # 1. Create and open the session.  A second ``session start`` sees this active
        # session in AdminBridge and cannot duplicate the scene/opening.
        async with self.db.unit_of_work() as s:
            session_row = await SessionService(s).create_session(
                campaign_id=campaign_id, attendance=attendance_member_ids
            )
            await SessionService(s).open_session(session_row.id)
            session_id, number = session_row.id, session_row.number

        prep_clues = list(prep.get("allowed_clues") or [])
        prep_npc_names = list(prep.get("present_npcs") or [])
        prep_npc_refs = await self._resolve_npc_refs(campaign_id, prep_npc_names)

        # 2. Resolve attendees and reminders from canonical character rows.
        ctx = await self._gather(campaign_id, attendance_member_ids, location_id)
        reminders = ctx["reminders"]

        # 3. Persist the canonical scene, positions, lifecycle, and DecisionWindow
        # BEFORE narration.  The model describes state; it never creates state.
        decision_window_id: str | None = None
        async with self.db.unit_of_work() as s:
            scene = await SceneService(s).create_scene(
                session_id=session_id, location_id=location_id, mode=mode,
                purpose=(
                    scene_purpose or prep.get("purpose") or ctx["central_question"]
                    or _DEFAULT_SCENE_PURPOSE
                ),
                dramatic_question=dramatic_question or ctx["central_question"],
                participants=participants or ctx["participant_refs"],
                visible_entity_ids=visible_entity_ids or prep_npc_refs,
                immediate_threat_ids=immediate_threat_ids or [],
                scene_start_game_time=ctx["game_time"],
            )
            # Imported allowed clues seed the scene (failure-with-teeth material).
            if prep_clues:
                scene.allowed_clues = prep_clues
            # Canonical placement — WITHOUT teleporting anyone. Only the campaign's
            # FIRST session may seat the whole party at the opening location; after
            # that, a character with a live position keeps it (a split party is a
            # fact, not an error), and only characters with NO live position (a late
            # joiner, a dangling location after re-import) are placed — at the
            # party's current anchor, never silently back at the campaign start.
            if location_id:
                from app.models.character import Character as _Char
                from app.models.location import Location as _Loc

                for ref in ctx["participant_refs"]:
                    _, cid = ref.split(":", 1)
                    char = await s.get(_Char, cid)
                    if char is None:
                        continue
                    current = (
                        await s.get(_Loc, char.location_id)
                        if char.location_id else None
                    )
                    has_live_position = (
                        current is not None and current.campaign_id == campaign_id
                    )
                    if number == 1 or not has_live_position:
                        char.location_id = location_id
                campaign_row = await s.get(Campaign, campaign_id)
                if campaign_row is not None:
                    campaign_row.current_party_anchor_id = location_id
            # Shared planning is automatic for 2+ eligible human-controlled PCs and
            # policy-driven for solo.  This is the opening's actual action gate, not
            # a decorative Ready row.
            policies = WindowPolicies.from_config(campaign_config)
            required_actor_ids = [
                ref.split(":", 1)[1]
                for ref in (participants or ctx["participant_refs"])
                if ref.startswith("character:")
            ]
            decision_window = None
            if policies.should_open_window(len(required_actor_ids)):
                window_mode = (
                    WindowMode.COMBAT
                    if mode == SceneMode.COMBAT
                    else WindowMode.NONCOMBAT
                )
                decision_window = await DecisionWindowService(s).open_window(
                    campaign_id=campaign_id,
                    session_id=session_id,
                    scene_id=scene.id,
                    round_id=1,
                    mode=window_mode,
                    required_actor_ids=required_actor_ids,
                    policies=policies,
                )
                decision_window_id = decision_window.id

            # Compatibility marker: old ``opening_cinematic_played`` installations
            # migrate in place.  v2 ignores the old prologue path but records which
            # player-facing pipeline produced future openings.
            camp_row = await s.get(Campaign, campaign_id)
            if camp_row is not None:
                camp_row.config = {
                    **(camp_row.config or {}),
                    "opening_cinematic_played": True,
                    "storytelling_pipeline_version": 2,
                }
            await SessionService(s).begin_active_play(session_id)
            row = await s.get(Session, session_id)
            row.active_play_state = ActivePlayState.TABLE_OPEN.value
            row.version += 1

            scene_id = scene.id

        # 4. Project the persisted state into a bounded packet, including the live
        # DecisionWindow.  Later-session injuries/effects/events naturally enter here;
        # no separate recap card or all-memory dump is needed.
        async with self.db.session() as read:
            scene = await read.get(Scene, scene_id)
            decision_window = (
                await read.get(DecisionWindow, decision_window_id)
                if decision_window_id else None
            )
            # The campaign's FIRST session opens with the grand, world-establishing
            # cinematic intro; later sessions open with the tighter in-scene opener.
            opening_mode = "campaign_opening" if number == 1 else "session_opening"
            packet = await ScenePacketBuilder(read).build(
                campaign_id=campaign_id,
                session_id=session_id,
                scene=scene,
                narration_mode=opening_mode,
                prep=prep,
                decision_window=decision_window,
            )

        # 5. Render one connected Thai scene.  The fallback receives the exact same
        # packet and therefore remains a scene rather than reverting to generic cards.
        opening = await self._generate_scene_opening(packet, profile=profile)
        frame_body = self._opening_body(opening)
        if not frame_body:
            opening = self._fallback_opening(packet)
            frame_body = self._opening_body(opening)
        decision_prompt = opening.decision_prompt.strip() or "พวกคุณจะทำอย่างไร?"

        actor_names = {c.id: c.name for c in packet.player_characters}
        planning_status = [
            f"○ **{actor_names.get(actor_id, actor_id)}** — รอการกระทำ"
            for actor_id in packet.shared_action_window.get("required_actor_ids", [])
        ]
        screen = cinematic_scene_screen(
            metadata=packet.metadata_line(),
            narration=frame_body,
            decision_prompt=decision_prompt,
            planning_window_id=decision_window_id,
            planning_status=planning_status,
        )
        frame_data = {
            "scene_metadata": packet.metadata_line(),
            "decision_prompt": decision_prompt,
            "session_id": session_id,
            "scene_id": scene_id,
            "location": packet.location,
            "storytelling_pipeline_version": 2,
            "decision_window_id": decision_window_id,
            "connected_scene": True,
        }
        messages = [OutboundMessage(
            channel_id,
            screen.to_text(),
            kind=MessageKind.SCENE_FRAME,
            title=None,
            data=frame_data,
            screen=screen,
        )]

        # Store the delivered prose and event evidence after generation.  No model
        # field is allowed to mutate mechanics or positions.
        used_hooks = list(dict.fromkeys(
            list(opening.used_hooks) + list(opening.used_character_facts)
        ))
        async with self.db.unit_of_work() as s:
            scene = await s.get(Scene, scene_id)
            if scene is not None:
                scene.spotlight = {
                    **(scene.spotlight or {}),
                    "last_narration": frame_body,
                    "opening_pipeline": "cinematic_scene_v2",
                }
                if not dramatic_question:
                    scene.dramatic_question = decision_prompt
                if not scene_purpose and opening.title:
                    scene.purpose = opening.title
            events = EventService(s)
            await events.record(
                campaign_id=campaign_id,
                session_id=session_id,
                event_type=EventType.SESSION_STARTED,
                actor_entity=SYSTEM_ACTOR,
                visibility=Visibility.PUBLIC,
                payload={
                    "number": number,
                    "summary": f"เริ่มเซสชันที่ {number} ณ {packet.location}",
                    "pipeline": "cinematic_scene_v2",
                },
                narrative_significance=20,
            )
            await events.record(
                campaign_id=campaign_id,
                session_id=session_id,
                scene_id=scene_id,
                event_type=EventType.SCENE_STARTED,
                actor_entity=SYSTEM_ACTOR,
                location_id=location_id,
                visibility=Visibility.PUBLIC,
                payload={
                    "summary": opening.title or f"เปิดฉาก ณ {packet.location}",
                    "used_hooks": used_hooks,
                    "pipeline": "cinematic_scene_v2",
                },
                narrative_significance=15,
            )

        log.info(
            "session opening delivered",
            extra={
                "campaign_id": campaign_id,
                "session_id": session_id,
                "channel_id": channel_id,
                "scene_id": scene_id,
                "location_id": location_id,
                "session_number": number,
                "state_version": row.version,
                "initialization_reason": "cinematic_scene_v2",
                "attendance": list(attendance_member_ids or []),
                "decision_window_id": decision_window_id,
            },
        )

        return OpeningResult(
            session_id=session_id, scene_id=scene_id, number=number,
            messages=messages, used_hooks=used_hooks,
            recap_text="",
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

            present_npc_names: list[str] = []
            if location_id:
                from sqlalchemy import select

                from app.models.npc import NPC

                npcs_here = (await read.execute(
                    select(NPC).where(NPC.campaign_id == campaign_id,
                                       NPC.current_location_id == location_id)
                )).scalars().all()
                present_npc_names = [n.name for n in npcs_here]
        return {
            "profile": profile,
            "characters": chars,
            "participant_refs": [f"character:{c.id}" for c in chars],
            "location_name": location.name if location else "-",
            "location_desc": location.description_obvious if location else "",
            "location_focused": location.description_focused if location else "",
            "game_time": campaign.current_game_time if campaign else 0,
            "reminders": reminders,
            "brief": campaign.brief if campaign else "",
            "central_question": campaign.central_question if campaign else "",
            "present_npc_names": present_npc_names,
        }

    async def _resolve_npc_refs(self, campaign_id: str, names: list[str]) -> list[str]:
        if not names:
            return []
        from sqlalchemy import select

        from app.models.npc import NPC

        refs = []
        async with self.db.session() as read:
            for name in names:
                npc = (await read.execute(
                    select(NPC).where(NPC.campaign_id == campaign_id, NPC.name == name)
                )).scalars().first()
                if npc is not None:
                    refs.append(f"npc:{npc.id}")
        return refs

    async def _generate_scene_opening(
        self, packet: ScenePacket, *, profile: dict
    ) -> OpeningScene:
        # The first session gets the grand, world-establishing opener; later sessions
        # the tighter in-scene one.  Both return the same OpeningScene shape.
        if packet.narration_mode == "campaign_opening":
            system_prompt = (
                THAI_DM_STYLE + "\n" + CAMPAIGN_OPENING_SYSTEM + "\n"
                + narration_template("campaign_opening")
            )
        else:
            system_prompt = (
                THAI_DM_STYLE + "\n" + OPENING_SYSTEM + "\n"
                + narration_template("session_opening")
            )
        messages: list[LLMMessage] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "NARRATIVE_PACING: CINEMATIC\n"
                    f"CAMPAIGN_STYLE: โทน={profile.get('tone', 'ตามข้อมูลแคมเปญ')}; "
                    f"สมดุล={profile.get('balance', 'ตามข้อมูลแคมเปญ')}\n"
                    + packet.to_prompt()
                ),
            },
        ]
        try:
            return await self.provider.generate_session_opening(messages)
        except LLMError as exc:
            log.warning(
                "cinematic scene generation failed; using grounded scene fallback: %s",
                exc,
            )
            return self._fallback_opening(packet)

    @staticmethod
    def _opening_body(opening: OpeningScene) -> str:
        paragraphs: list[str] = []
        if opening.narration.strip():
            paragraphs.append(opening.narration.strip())
        elif opening.situation_lines:
            paragraphs.append("\n\n".join(
                line.strip() for line in opening.situation_lines if line.strip()
            ))
        if opening.pressure.strip():
            paragraphs.append(opening.pressure.strip())
        return "\n\n".join(paragraphs).strip()

    @staticmethod
    def _fallback_opening(packet: ScenePacket) -> OpeningScene:
        """A deterministic, connected scene built ONLY from packet facts.

        It fires when the model is unreachable.  It invents no sensory detail, but it
        also never degrades into a "name — stat; stat; stat" status list: each fact is
        woven into a sentence that places the party inside a scene already in motion,
        honouring the cinematic contract instead of a campaign-summary card.  A sparse
        campaign still gets a real (if spare) scene, never generic sections.
        """
        place = packet.location
        used: list[str] = []
        paragraphs: list[str] = []

        # 0) The campaign's FIRST opening establishes the world first (from the brief +
        #    PUBLIC canon) so even the offline scene reads as an epic introduction, not
        #    a bare room.  Bracketed category tags are stripped for prose.
        if packet.narration_mode == "campaign_opening":
            world_bits: list[str] = []
            if packet.campaign_context:
                world_bits.append(packet.campaign_context)
            for fact in packet.world_canon[:3]:
                clean = fact.split("] ", 1)[1] if fact.startswith("[") and "] " in fact else fact
                world_bits.append(clean)
            if world_bits:
                paragraphs.append(" ".join(world_bits))

        # 1) Where / when / what it feels like — one grounded establishing beat.
        opening = packet.location_description or f"พวกคุณมาหยุดอยู่ที่{place}"
        ambience = [x for x in (packet.weather, packet.lighting) if x]
        ambience += [c for c in packet.environmental_conditions[:2] if c]
        if ambience:
            opening = f"{opening}\nรอบตัวพวกคุณ {' '.join(ambience)}"
        paragraphs.append(opening.strip())

        # 2) Rest the camera on each character in turn, weaving ONE grounded detail
        #    into an active placement — never a bulleted dump of everything stored.
        char_lines: list[str] = []
        for index, char in enumerate(packet.player_characters):
            detail = ""
            if char.appearance:
                detail = char.appearance
                used.append(f"{char.name}.appearance")
            elif char.active_effects:
                detail = "แสงของ " + ", ".join(char.active_effects[:1]) + " ยังพราวอยู่รอบตัว"
                used.append(f"{char.name}.active_effects")
            elif char.equipment:
                detail = "มือแนบอยู่กับ" + char.equipment[0]
                used.append(f"{char.name}.equipment")
            elif char.injuries:
                detail = char.injuries
                used.append(f"{char.name}.injuries")
            elif char.conditions:
                detail = "ยังตกอยู่ใต้สภาวะ " + ", ".join(char.conditions[:1])
                used.append(f"{char.name}.conditions")
            stem = "ยืนอยู่ตรงนี้" if index == 0 else "ยืนอยู่ข้าง ๆ กัน"
            line = f"{char.name} {stem}" + (f" {detail}" if detail else "")
            facts = list(char.relevant_facts.items())
            if facts:
                key, fact = facts[0]
                line += f" สิ่งที่พาเขามาถึงที่นี่คือ{fact}"
                used.append(f"{char.name}.{key}")
            char_lines.append(line)
        if char_lines:
            paragraphs.append("\n".join(char_lines))

        # 3) The world is already doing something — verbatim, not paraphrased.
        activity: list[str] = []
        if packet.current_activity:
            activity.append(packet.current_activity)
        for npc in packet.npcs_present:
            if npc.current_activity:
                activity.append(f"{npc.name}{npc.current_activity}")
            elif npc.name not in packet.current_activity:
                activity.append(f"{npc.name}ยังอยู่ในบริเวณเดียวกัน")
        if activity:
            paragraphs.append(" ขณะเดียวกัน ".join(activity))

        # 4) Why the party is here and what the clock is doing — woven, not carded.
        direction: list[str] = []
        reason = packet.reason_party_is_here
        if reason and reason != _DEFAULT_SCENE_PURPOSE:
            direction.append(f"พวกคุณมาถึง{place}นี้เพราะ{reason}")
        for objective in packet.active_objectives[:1]:
            if not reason or objective not in reason:
                direction.append(objective)
        if packet.immediate_threats:
            direction.append(packet.immediate_threats[0])
        if packet.delay_stakes:
            direction.append(f"และหากรีรอ {packet.delay_stakes}")
        if direction:
            paragraphs.append(" ".join(direction))

        return OpeningScene(
            title=f"เปิดฉาก ณ {place}",
            narration="\n\n".join(p for p in paragraphs if p.strip()),
            decision_prompt="พวกคุณจะทำอย่างไร?",
            used_character_facts=used,
        )
