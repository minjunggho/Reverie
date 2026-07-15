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
from app.ai.prompts.system_prompts import OPENING_SYSTEM, PROLOGUE_SYSTEM
from app.ai.prompts.thai_dm_style import THAI_DM_STYLE
from app.core.clock import format_game_time_th
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
from app.schemas.llm_io import CampaignPrologue, OpeningScene
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

        # 1. create + open the session. Imported Session Prep, when present,
        #    constrains WHAT is happening at the opening (activity, cast, clues) —
        #    the DM's obligation not to discard the campaign.
        async with self.db.session() as read:
            from app.models.campaign import Campaign

            campaign = await read.get(Campaign, campaign_id)
            prep = dict((campaign.session_prep if campaign else None) or {})
            # The cinematic opening is governed by persisted state, never by session
            # numbering — a campaign whose first session was consumed by a broken
            # start still gets its opening on the next session.
            cinematic_played = bool(
                ((campaign.config if campaign else None) or {}).get(
                    "opening_cinematic_played"))

        async with self.db.unit_of_work() as s:
            session_row = await SessionService(s).create_session(
                campaign_id=campaign_id, attendance=attendance_member_ids
            )
            await SessionService(s).open_session(session_row.id)
            session_id, number = session_row.id, session_row.number

        # WHERE to open is the CALLER's canonical resolution (anchor → positions →
        # starting location → legacy prep → owner's explicit choice). Prep supplies
        # the WHAT of Session 1 — activity, cast, clues — never a teleport.
        prep_clues = list(prep.get("allowed_clues") or [])
        prep_activity = prep.get("current_activity") or ""
        prep_npc_names = list(prep.get("present_npcs") or [])
        prep_npc_refs = await self._resolve_npc_refs(campaign_id, prep_npc_names)

        # 2. gather the bounded opening context (no DM-only records).
        ctx = await self._gather(campaign_id, attendance_member_ids, location_id)
        reminders = ctx["reminders"]

        # 3. build the opening. The grand cinematic prologue plays EXACTLY ONCE per
        #    campaign — whenever the played-once flag is unset and canon provides a
        #    main goal — regardless of session number or how the location was chosen
        #    (inferred or `start at`). Resuming later sessions never replays it.
        prologue: CampaignPrologue | None = None
        if not cinematic_played:
            world = await self._gather_world_canon(campaign_id, location_id)
            if world["main_goal"]:
                prologue = await self._generate_cinematic_prologue(ctx, world, prep=prep)
        if prologue is not None:
            # The prologue's first beat + first choice ARE the opening scene; the
            # world-scale movements are rendered ahead of it in step 5.
            opening = OpeningScene(
                title=prologue.title,
                situation_lines=[prologue.first_beat],
                pressure="",
                decision_prompt=prologue.decision_prompt,
                used_hooks=prologue.used_hooks,
            )
            recap_text = ""
        elif number == 1:
            opening = await self._generate_first_opening(
                ctx, prep.get("purpose") or scene_purpose, prep=prep)
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
            # The cinematic played — persist the played-once flag in the SAME
            # transaction as the scene/lifecycle so it can never fire twice.
            if prologue is not None:
                camp_row = await s.get(Campaign, campaign_id)
                if camp_row is not None:
                    camp_row.config = {
                        **(camp_row.config or {}), "opening_cinematic_played": True,
                    }
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
            data={"footer": f"{ctx['location_name']} · {format_game_time_th(ctx['game_time'])}"},
        )]
        if recap_text:
            messages.append(OutboundMessage(
                channel_id, recap_text, kind=MessageKind.PLAYER_SAFE_RECAP,
                title="ความเดิมตอนที่แล้ว",
            ))
        # The cinematic prologue's world-scale movements are their own messages so
        # the grand moments can breathe before the scene zooms in on the party.
        if prologue is not None:
            messages.extend(self._prologue_messages(channel_id, prologue))
        frame_body = "\n".join(opening.situation_lines)
        if opening.pressure:
            frame_body += f"\n\n{opening.pressure}"
        frame_data: dict = {"decision_prompt": opening.decision_prompt or None}
        fields: list[dict] = []
        # The main goal is the campaign's through-line — surfaced unmistakably so the
        # players leave the opening knowing what they are ultimately trying to do.
        if prologue is not None and prologue.main_goal:
            fields.append({
                "name": "🎯 เป้าหมายหลักของการเดินทาง", "value": prologue.main_goal,
                "inline": False,
            })
        if reminders:
            fields.append({
                "name": "เตือนความจำ", "value": "\n".join(f"• {r}" for r in reminders),
                "inline": False,
            })
        if fields:
            frame_data["fields"] = fields
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
            "location_focused": location.description_focused if location else "",
            "game_time": campaign.current_game_time if campaign else 0,
            "reminders": reminders,
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

    async def _generate_first_opening(self, ctx: dict, scene_purpose: str,
                                      *, prep: dict | None = None) -> OpeningScene:
        char_lines = []
        for c in ctx["characters"]:
            hooks = c.hooks or {}
            hook_str = "; ".join(f"{k}={v}" for k, v in hooks.items() if v) or "-"
            char_lines.append(f"- {c.name} ({c.char_class}): {hook_str}")
        profile = ctx["profile"]
        prep = prep or {}
        # Imported prep constrains the story facts the opening MUST honour.
        prep_block = ""
        if prep:
            prep_block = (
                f"\nSESSION_PREP (ต้องใช้ ห้ามทิ้ง):\n"
                f"- current_activity: {prep.get('current_activity', '-')}\n"
                f"- present_npcs: {', '.join(prep.get('present_npcs') or []) or '-'}\n"
                f"- allowed_clues: {', '.join(prep.get('allowed_clues') or []) or '-'}\n"
                f"- do_not_reveal: {', '.join(prep.get('do_not_reveal') or []) or '-'}"
            )
        messages: list[LLMMessage] = [
            {"role": "system", "content": THAI_DM_STYLE + "\n" + OPENING_SYSTEM},
            {"role": "user", "content": (
                f"PROFILE: โทน={profile.get('tone', 'ผจญภัยคลาสสิก')}; "
                f"สไตล์={profile.get('balance', 'สมดุล')}\n"
                f"CHARACTERS:\n" + "\n".join(char_lines) + "\n"
                f"LOCATION: {ctx['location_name']} — {ctx['location_desc']}\n"
                f"PURPOSE: {scene_purpose or '-'}" + prep_block
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

    # --- cinematic prologue (session 1, world-scale) ---------------------------
    async def _gather_world_canon(self, campaign_id: str, location_id: str) -> dict:
        """Priority-ordered, PLAYER-SAFE world context for the cinematic prologue,
        following the campaign data priority: brief → main quest → plotline/Act-I →
        world facts → rules/tone → starting location + geography → NPCs.

        Nothing DM-only ever enters: brief/central-question are player-facing by
        construction; lore is PUBLIC canon only; powers are NAMES only (never their
        hidden goals/next moves); the geography ladder and present NPCs are physical
        facts a newcomer would perceive. Anything tagged SECRET is defensively dropped
        as a second line of defence behind the visibility filter."""
        from sqlalchemy import select

        from app.models.location import Location
        from app.models.npc import NPC
        from app.models.world import Threat
        from app.models.world_graph import CampaignCanonRecord
        from app.services.campaigns.main_story import MainStoryService

        _LORE = {"world_fact", "history", "religion", "magic", "politics", "culture"}

        def _safe(text: str | None) -> bool:
            return bool(text) and "SECRET_" not in text

        async with self.db.session() as read:
            campaign = await read.get(Campaign, campaign_id)
            brief = campaign.brief if campaign else ""
            central_q = campaign.central_question if campaign else ""
            profile = (campaign.config or {}).get("profile", {}) if campaign else {}
            story = await MainStoryService(read).get(campaign_id)
            main_goal = next(
                (g.get("text", "") for g in story.get("goals", [])
                 if g.get("key") == "main" and g.get("text")),
                "",
            ) or central_q
            lore_rows = (await read.execute(select(CampaignCanonRecord).where(
                CampaignCanonRecord.campaign_id == campaign_id,
                CampaignCanonRecord.visibility == Visibility.PUBLIC.value,
            ))).scalars().all()
            lore = [r.fact for r in sorted(lore_rows, key=lambda r: r.importance, reverse=True)
                    if r.category in _LORE and _safe(r.fact)][:12]
            powers = [t.name for t in (await read.execute(select(Threat).where(
                Threat.campaign_id == campaign_id, Threat.status == "active",
            ))).scalars().all() if _safe(t.name)][:8]
            # Geography ladder for the cinematic descent: walk parent_id from the
            # opening place up to the world (bounded), then reverse so it reads
            # largest → the exact place — the path the camera follows.
            ladder: list[tuple[str, str]] = []
            cur = await read.get(Location, location_id) if location_id else None
            guard = 0
            while cur is not None and guard < 6:
                ladder.append(((cur.location_type or "LOCATION"), cur.name))
                cur = await read.get(Location, cur.parent_id) if cur.parent_id else None
                guard += 1
            ladder.reverse()
            # NPCs a newcomer would see standing here — names + a player-safe
            # descriptor (voice/manner) only; never their goals or beliefs.
            npc_rows = (await read.execute(select(NPC).where(
                NPC.campaign_id == campaign_id,
                NPC.current_location_id == location_id,
            ))).scalars().all()
            npcs_present = [(n.name, (n.voice_register or "").strip())
                            for n in npc_rows if _safe(n.name)][:6]
        return {
            "brief": brief, "main_goal": main_goal, "state": story.get("state", ""),
            "lore": lore, "powers": powers,
            "tone": profile.get("tone", ""), "balance": profile.get("balance", ""),
            "boundaries": [b for b in (profile.get("boundaries") or []) if _safe(b)],
            "geography": ladder, "npcs_present": npcs_present,
        }

    async def _generate_cinematic_prologue(
        self, ctx: dict, world: dict, *, prep: dict | None = None
    ) -> CampaignPrologue | None:
        """Grow the grand opening from player-safe canon. Returns None (caller falls
        back to the standard opening) if the model cannot produce a valid prologue."""
        char_lines = []
        for c in ctx["characters"]:
            hooks = c.hooks or {}
            hook_str = "; ".join(f"{k}={v}" for k, v in hooks.items() if v) or "-"
            char_lines.append(f"- {c.name} ({c.char_class}): {hook_str}")
        prep = prep or {}
        lore_block = "\n".join(f"- {f}" for f in world["lore"]) or "-"
        powers_block = ", ".join(world["powers"]) or "-"
        # The descent path, largest → the exact place, e.g. "WORLD:… → REGION:… → LOCATION:…".
        geo_block = " → ".join(f"{kind}:{name}" for kind, name in world["geography"]) \
            or ctx["location_name"]
        npc_block = "; ".join(f"{name} ({desc})" if desc else name
                              for name, desc in world["npcs_present"]) or "-"
        present = ", ".join(prep.get("present_npcs") or []) or npc_block
        clues = ", ".join(prep.get("allowed_clues") or []) or "-"
        do_not = ", ".join(prep.get("do_not_reveal") or []) or "-"
        tone = world["tone"] or ctx["profile"].get("tone", "-")
        boundaries = ", ".join(world["boundaries"]) or "-"
        messages: list[LLMMessage] = [
            {"role": "system", "content": THAI_DM_STYLE + "\n" + PROLOGUE_SYSTEM},
            {"role": "user", "content": (
                f"WORLD_BRIEF: {world['brief'] or '-'}\n"
                f"MAIN_GOAL (เป้าหมายหลักหนึ่งข้อที่ผู้เล่นต้องรู้ชัด): {world['main_goal']}\n"
                f"PLOTLINE_ACT_I (สิ่งที่กำลังเกิดตอนเปิดฉาก — ห้ามทิ้ง):\n"
                f"- current_activity: {prep.get('current_activity') or '-'}\n"
                f"- purpose: {prep.get('purpose') or '-'}\n"
                f"- present_npcs: {present}\n"
                f"- clues_allowed: {clues}\n"
                f"- story_state: {world['state'] or '-'}\n"
                f"WORLD_FACTS (canon ที่ผู้เล่นรู้ได้ เรียงตามสำคัญ):\n{lore_block}\n"
                f"KNOWN_POWERS_AND_DANGERS (ใช้ชื่อเหล่านี้เท่านั้น): {powers_block}\n"
                f"TONE: {tone}; ขอบเขตห้ามข้าม: {boundaries}\n"
                f"GEOGRAPHY_LADDER (ซูมจากใหญ่ไปเล็กตามนี้ ให้จบที่ OPENING_PLACE): {geo_block}\n"
                f"OPENING_PLACE: {ctx['location_name']} — {ctx['location_desc']}\n"
                f"OPENING_PLACE_CLOSER (เมื่อมองใกล้ขึ้น): {ctx.get('location_focused') or '-'}\n"
                f"NPCS_PRESENT (อยู่ตรงนั้นตอนนี้): {npc_block}\n"
                f"DO_NOT_REVEAL (ห้ามเปิดเผยเด็ดขาด): {do_not}\n"
                f"CHARACTERS:\n" + "\n".join(char_lines)
            )},
        ]
        try:
            prologue = await self.provider.generate_campaign_prologue(messages)
        except LLMError as exc:
            log.warning("cinematic prologue failed; using standard opening: %s", exc)
            return None
        # The engine, not the model, guarantees the players are told the main goal:
        # if the model dropped it, restore the campaign's canonical objective.
        if not prologue.main_goal.strip():
            prologue = prologue.model_copy(update={"main_goal": world["main_goal"]})
        return prologue

    def _prologue_messages(
        self, channel_id: str, prologue: CampaignPrologue
    ) -> list[OutboundMessage]:
        """Render the world-scale movements as their own frames, large to small, so
        each grand beat LANDS before the next one arrives — the world & its powers,
        then the conflict that changed everything, then the camera's descent to the
        party. Splitting them (rather than one wall of text) is what lets the opening
        breathe like a film's cold open."""
        world_powers = "\n\n".join(part for part in (prologue.world, prologue.powers)
                                   if part.strip())
        descent = "\n\n".join(part for part in (prologue.approach, prologue.the_party)
                              if part.strip())
        beats: list[tuple[str, str | None]] = [
            (world_powers, prologue.title),           # the world & its powers
            (prologue.crisis, "เหตุการณ์ที่เปลี่ยนทุกอย่าง"),  # the great conflict
            (descent, "เส้นทางสู่พวกเจ้า"),            # zoom in to the party
        ]
        return [
            OutboundMessage(channel_id, body, kind=MessageKind.CAMPAIGN_PROLOGUE,
                            title=title)
            for body, title in beats if body.strip()
        ]

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
