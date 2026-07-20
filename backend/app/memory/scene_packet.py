"""Bounded, canonical context for player-facing narration.

``ScenePacket`` is deliberately a projection, not a memory dump.  It contains only
facts useful to the current beat, caps every collection, filters event visibility in
SQL, and marks private NPC/threat direction as behaviour guidance rather than
player-known exposition.  Missing character facts stay missing; the narrator is
explicitly forbidden to fill those blanks.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import DAY, HOUR, day_segment_th
from app.models.campaign import Campaign
from app.models.character import Character
from app.models.consequences import ACTIONABLE_QUEST_STATES, Quest
from app.models.decision_window import DecisionWindow
from app.models.enums import Visibility
from app.models.items import InventoryEntry, ItemDefinition
from app.models.location import Location
from app.models.npc import NPC
from app.models.progression import ActiveEffect
from app.models.world import ScheduledWorldEvent, Threat
from app.models.world_graph import CampaignCanonRecord
from app.services.campaigns.main_story import MainStoryService
from app.services.events import EventService

MAX_CHARACTERS = 8
MAX_ITEMS_PER_CHARACTER = 4
MAX_HOOKS_PER_CHARACTER = 3
MAX_NPCS = 8
MAX_OBJECTS = 10
MAX_EVENTS = 6
MAX_OBJECTIVES = 5
MAX_PRESSURES = 5
MAX_CANON = 6

_HOOK_PRIORITY = (
    "objective", "short_term_goal", "long_term_goal", "desire", "goal",
    "connection", "bond", "ideal", "vow", "fear", "flaw", "origin", "concept",
)
_IDENTITY_KEYS = (
    "short_term_goal", "long_term_goal", "goals", "reason_for_adventuring",
    "bonds", "ideals", "fears", "rivals", "mentors", "family", "friends",
    "past_events", "homeland", "culture",
)


def _text(value: Any, *, limit: int = 360) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        out = value.strip()
    elif isinstance(value, (list, tuple)):
        out = "; ".join(str(x).strip() for x in value if str(x).strip())
    elif isinstance(value, dict):
        out = "; ".join(f"{k}: {v}" for k, v in value.items() if v)
    else:
        out = str(value).strip()
    return out[:limit]


def _terms(*values: Any) -> set[str]:
    import re

    text = " ".join(_text(v, limit=1000).casefold() for v in values)
    return set(re.findall(r"[\wก-๙]+", text))


def _relevant(value: str, relevance_terms: set[str]) -> bool:
    words = _terms(value)
    return bool(words & relevance_terms)


@dataclass
class SceneCharacter:
    id: str
    name: str
    position: str
    pronouns: str = ""
    appearance: str = ""
    equipment: list[str] = field(default_factory=list)
    relevant_facts: dict[str, str] = field(default_factory=dict)
    injuries: str = ""
    conditions: list[str] = field(default_factory=list)
    active_effects: list[str] = field(default_factory=list)


@dataclass
class SceneNPC:
    id: str
    name: str
    position: str
    visible_description: str = ""
    current_activity: str = ""
    emotional_state: str = ""
    relationship_to_party: str = ""
    behaviour_goal: str = ""
    physical_state: str = ""


@dataclass
class ScenePacket:
    session_id: str
    campaign_id: str
    scene_id: str
    language: str = "th"
    narration_mode: str = "session_opening"
    game_time: int = 0
    day: int = 1
    clock: str = "00:00"
    day_segment: str = "กลางดึก"
    campaign_context: str = ""
    # PUBLIC/PARTY world canon (era, powers, religion, history) — populated only for a
    # campaign's FIRST opening so the epic intro can name real powers/faith without
    # inventing them.  Never carries DM-only truth (SQL-filtered by visibility).
    world_canon: list[str] = field(default_factory=list)
    location: str = "-"
    location_description: str = ""
    parent_location: str = ""
    weather: str = ""
    lighting: str = ""
    environmental_conditions: list[str] = field(default_factory=list)
    active_hazards: list[str] = field(default_factory=list)
    player_characters: list[SceneCharacter] = field(default_factory=list)
    npcs_present: list[SceneNPC] = field(default_factory=list)
    visible_enemies: list[str] = field(default_factory=list)
    positions_and_distances: list[str] = field(default_factory=list)
    interactable_objects: list[str] = field(default_factory=list)
    known_clues: list[str] = field(default_factory=list)
    active_objectives: list[str] = field(default_factory=list)
    immediate_threats: list[str] = field(default_factory=list)
    threat_clocks: list[str] = field(default_factory=list)
    recent_events: list[str] = field(default_factory=list)
    unresolved_consequences: list[str] = field(default_factory=list)
    pending_rolls: list[str] = field(default_factory=list)
    shared_action_window: dict[str, Any] = field(default_factory=dict)
    current_activity: str = ""
    reason_party_is_here: str = ""
    delay_stakes: str = ""
    do_not_reveal: list[str] = field(default_factory=list)

    def metadata_line(self) -> str:
        condition = self.weather or "สภาพอากาศไม่ระบุ"
        return (
            f"| {self.day_segment} | {self.clock} น. | วันที่ {self.day} | "
            f"{self.location} | {condition} |"
        )

    def to_prompt(self) -> str:
        """Serialize without empty fields so absence can never look like an invitation
        to invent.  The labels are stable enough for prompt/golden rubric tests."""
        lines = [
            "SCENE_PACKET (ข้อเท็จจริงที่อนุญาตสำหรับฉากนี้เท่านั้น)",
            f"session_id: {self.session_id}",
            f"campaign_id: {self.campaign_id}",
            f"scene_id: {self.scene_id}",
            f"language: {self.language}",
            f"narration_mode: {self.narration_mode}",
            f"time: {self.day_segment}; {self.clock}; วันที่ {self.day}",
            f"location: {self.location}",
        ]
        scalar_fields = (
            ("campaign_context", self.campaign_context),
            ("location_description", self.location_description),
            ("parent_location", self.parent_location),
            ("weather", self.weather),
            ("lighting", self.lighting),
            ("current_activity", self.current_activity),
            ("reason_party_is_here", self.reason_party_is_here),
            ("delay_stakes", self.delay_stakes),
        )
        lines.extend(f"{key}: {value}" for key, value in scalar_fields if value)
        list_fields = (
            ("world_canon", self.world_canon),
            ("environmental_conditions", self.environmental_conditions),
            ("active_hazards", self.active_hazards),
            ("visible_enemies", self.visible_enemies),
            ("positions_and_distances", self.positions_and_distances),
            ("interactable_objects", self.interactable_objects),
            ("known_clues", self.known_clues),
            ("active_objectives", self.active_objectives),
            ("immediate_threats", self.immediate_threats),
            ("threat_clocks", self.threat_clocks),
            ("recent_events", self.recent_events),
            ("unresolved_consequences", self.unresolved_consequences),
            ("pending_rolls", self.pending_rolls),
        )
        for key, values in list_fields:
            if values:
                lines.append(f"{key}:\n" + "\n".join(f"- {v}" for v in values))
        if self.player_characters:
            lines.append("player_characters:")
            for char in self.player_characters:
                data = {k: v for k, v in asdict(char).items() if v}
                lines.append(f"- {data}")
        if self.npcs_present:
            lines.append("npcs_present:")
            for npc in self.npcs_present:
                data = {k: v for k, v in asdict(npc).items() if v}
                lines.append(f"- {data}")
        if self.shared_action_window:
            lines.append(f"shared_action_window: {self.shared_action_window}")
        if self.do_not_reveal:
            lines.append("do_not_reveal:\n" + "\n".join(f"- {v}" for v in self.do_not_reveal))
        lines.append(
            "GROUNDING: ช่องที่ไม่มีใน packet คือไม่ทราบ ห้ามเติมรูปลักษณ์ อดีต "
            "อารมณ์ ความเชื่อ ความสัมพันธ์ ของถาวร ผลทอย หรือผลกลไกขึ้นเอง"
        )
        return "\n".join(lines)


class ScenePacketBuilder:
    """Select the small slice of canonical state useful to one narration beat."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build(
        self, *, campaign_id: str, session_id: str, scene,
        narration_mode: str = "session_opening", prep: dict | None = None,
        decision_window: DecisionWindow | None = None,
    ) -> ScenePacket:
        prep = dict(prep or {})
        campaign = await self.session.get(Campaign, campaign_id)
        location = await self.session.get(Location, scene.location_id) if scene.location_id else None
        game_time = int(campaign.current_game_time if campaign else 0)
        state = dict(location.state or {}) if location else {}
        story = await MainStoryService(self.session).get(campaign_id)
        main_goal = next(
            (_text(g.get("text")) for g in story.get("goals", [])
             if g.get("key") == "main" and g.get("status", "open") == "open" and g.get("text")),
            "",
        ) or _text(campaign.central_question if campaign else "")
        reason = _text(
            prep.get("purpose") or scene.purpose or main_goal
            or (campaign.central_question if campaign else "")
        )
        relevance_terms = _terms(
            location.name if location else "", location.description_obvious if location else "",
            location.current_activity if location else "", reason, main_goal,
            prep.get("current_activity"), prep.get("allowed_clues"),
        )
        packet = ScenePacket(
            session_id=session_id,
            campaign_id=campaign_id,
            scene_id=scene.id,
            language="th",
            narration_mode=narration_mode,
            game_time=game_time,
            day=game_time // DAY + 1,
            clock=f"{(game_time % DAY) // HOUR:02d}:{game_time % HOUR:02d}",
            day_segment=day_segment_th(game_time),
            campaign_context=_text(campaign.brief if campaign else "", limit=600),
            location=location.name if location else "-",
            location_description=_text(location.description_obvious if location else ""),
            weather=_text(location.weather if location else ""),
            lighting=_text(state.get("lighting")),
            environmental_conditions=self._values(
                state, "environment", "conditions", "temperature", "terrain", limit=6),
            active_hazards=self._values(state, "hazards", "active_hazards", limit=5),
            interactable_objects=[
                _text(x) for x in list(location.contents or [])[:MAX_OBJECTS] if _text(x)
            ] if location else [],
            known_clues=[_text(x) for x in list(scene.allowed_clues or [])[:6] if _text(x)],
            current_activity=_text(
                prep.get("current_activity") or (location.current_activity if location else "")
            ),
            reason_party_is_here=reason,
            do_not_reveal=[_text(x) for x in list(prep.get("do_not_reveal") or [])[:8] if _text(x)],
        )
        packet.parent_location = await self._parent_path(location)
        packet.player_characters = await self._characters(
            campaign_id, scene, packet.location, relevance_terms)
        packet.npcs_present = await self._npcs(
            campaign_id, scene, packet.location, prep, relevance_terms)
        packet.positions_and_distances = [
            f"{c.name}: {c.position}" for c in packet.player_characters
        ] + [f"{n.name}: {n.position}" for n in packet.npcs_present]
        packet.active_objectives = await self._objectives(campaign_id, main_goal)
        (
            packet.immediate_threats,
            packet.threat_clocks,
            packet.delay_stakes,
        ) = await self._pressures(campaign_id, scene, prep, game_time)
        packet.recent_events, packet.unresolved_consequences = await self._events(
            campaign_id, session_id)
        if scene.pending_action:
            pending = dict(scene.pending_action)
            label = _text(pending.get("check_label") or pending.get("kind") or "มีการทอยค้างอยู่")
            packet.pending_rolls = [label] if label else []
        # The campaign's FIRST opening may name real powers/faith/history — bounded and
        # PUBLIC only, so the epic intro is grounded, never invented.
        if narration_mode == "campaign_opening":
            packet.world_canon = await self._world_canon(campaign_id)
        if decision_window is not None:
            packet.shared_action_window = {
                "window_id": decision_window.id,
                "phase": decision_window.phase,
                "round_id": decision_window.round_id,
                "required_actor_ids": list(decision_window.required_actor_ids or []),
                "solo_auto_ready": bool(
                    decision_window.config.get("single_player_auto_ready", True)),
            }
        return packet

    async def _world_canon(self, campaign_id: str) -> list[str]:
        """Top PUBLIC/PARTY world-canon facts (powers, faith, history) for the epic
        first-session intro.  DM-only truth is excluded in SQL, never by the model."""
        rows = list((await self.session.execute(
            select(CampaignCanonRecord).where(
                CampaignCanonRecord.campaign_id == campaign_id,
                CampaignCanonRecord.active.is_(True),
                CampaignCanonRecord.visibility.in_(
                    [Visibility.PUBLIC.value, Visibility.PARTY.value]),
            ).order_by(CampaignCanonRecord.importance.desc())
        )).scalars())
        out: list[str] = []
        for row in rows:
            fact = _text(row.fact)
            if fact:
                out.append(f"[{row.category}] {fact}" if row.category else fact)
        return list(dict.fromkeys(out))[:MAX_CANON]

    @staticmethod
    def _values(data: dict, *keys: str, limit: int) -> list[str]:
        out: list[str] = []
        for key in keys:
            value = data.get(key)
            values = value if isinstance(value, list) else [value]
            out.extend(_text(v) for v in values if _text(v))
        return list(dict.fromkeys(out))[:limit]

    async def _parent_path(self, location: Location | None) -> str:
        names: list[str] = []
        cur, guard = location, 0
        while cur is not None and cur.parent_id and guard < 5:
            cur = await self.session.get(Location, cur.parent_id)
            if cur is None:
                break
            names.append(cur.name)
            guard += 1
        return " · ".join(reversed(names))

    async def _characters(
        self, campaign_id: str, scene, location_name: str, relevance_terms: set[str],
    ) -> list[SceneCharacter]:
        refs = [r for r in (scene.participants or []) if r.startswith("character:")]
        out: list[SceneCharacter] = []
        for ref in refs[:MAX_CHARACTERS]:
            char = await self.session.get(Character, ref.split(":", 1)[1])
            if char is None or char.campaign_id != campaign_id:
                continue
            identity = dict(char.identity or {})
            hooks = {**dict(char.hooks or {})}
            for key in _IDENTITY_KEYS:
                if identity.get(key) and key not in hooks:
                    hooks[key] = identity[key]
            selected: dict[str, str] = {}
            for key in _HOOK_PRIORITY:
                value = _text(hooks.get(key))
                if not value or len(selected) >= MAX_HOOKS_PER_CHARACTER:
                    continue
                if _relevant(value, relevance_terms):
                    selected[key] = value
            # At an opening, one stored motivation/connection is relevant to why this
            # person is here even when Thai token overlap is sparse.
            if not selected:
                for key in _HOOK_PRIORITY:
                    value = _text(hooks.get(key))
                    if value:
                        selected[key] = value
                        break
            equipment = await self._equipment(char.id, relevance_terms)
            effects = list((await self.session.execute(select(ActiveEffect).where(
                ActiveEffect.character_id == char.id,
                ActiveEffect.campaign_id == campaign_id,
                ActiveEffect.active.is_(True),
            ))).scalars())
            injury = ""
            if char.hp < char.max_hp:
                injury = f"บาดเจ็บ (HP {char.hp}/{char.max_hp})"
            elif char.temp_hp:
                injury = f"มีพลังชีวิตชั่วคราว {char.temp_hp}"
            out.append(SceneCharacter(
                id=char.id,
                name=char.name,
                position=f"อยู่ที่{location_name}",
                pronouns=_text(identity.get("pronouns"), limit=80),
                appearance=_text(char.appearance),
                equipment=equipment,
                relevant_facts=selected,
                injuries=injury,
                conditions=[_text(x, limit=100) for x in list(char.conditions or [])[:6] if _text(x)],
                active_effects=[_text(e.name, limit=120) for e in effects[:6] if _text(e.name)],
            ))
        return out

    async def _equipment(self, character_id: str, relevance_terms: set[str]) -> list[str]:
        rows = list((await self.session.execute(
            select(InventoryEntry, ItemDefinition)
            .join(ItemDefinition, ItemDefinition.id == InventoryEntry.item_definition_id)
            .where(InventoryEntry.character_id == character_id)
            .order_by(InventoryEntry.equipped.desc(), ItemDefinition.name)
        )).all())
        ranked = sorted(
            rows,
            key=lambda row: (
                not row[0].equipped,
                not _relevant(f"{row[1].name} {row[1].description}", relevance_terms),
                row[1].name,
            ),
        )
        return [
            f"{item.name}{' (สวม/ถืออยู่)' if entry.equipped else ''}"
            for entry, item in ranked[:MAX_ITEMS_PER_CHARACTER]
        ]

    async def _npcs(
        self, campaign_id: str, scene, location_name: str, prep: dict,
        relevance_terms: set[str],
    ) -> list[SceneNPC]:
        rows = list((await self.session.execute(select(NPC).where(
            NPC.campaign_id == campaign_id,
            NPC.current_location_id == scene.location_id,
            NPC.available.is_(True),
        ))).scalars())
        prep_names = set(prep.get("present_npcs") or [])
        rows.sort(key=lambda n: (n.name not in prep_names, n.name))
        activity_map = dict(prep.get("npc_activities") or {})
        out: list[SceneNPC] = []
        for npc in rows[:MAX_NPCS]:
            goal = next((_text(x) for x in list(npc.goals or []) if _text(x)), "")
            # Goal is direction for visible behaviour only.  The opening prompt says
            # never to state it as internal knowledge.
            out.append(SceneNPC(
                id=npc.id,
                name=npc.name,
                position=f"อยู่ที่{location_name}",
                visible_description=_text(npc.voice_register, limit=180),
                current_activity=_text(activity_map.get(npc.name)),
                emotional_state=_text(npc.emotional_state, limit=80),
                relationship_to_party=_text(
                    (npc.attitudes or {}).get("party"), limit=100),
                behaviour_goal=goal if _relevant(goal, relevance_terms) or npc.name in prep_names else "",
                physical_state=_text(npc.physical_state, limit=80),
            ))
        return out

    async def _objectives(self, campaign_id: str, main_goal: str) -> list[str]:
        objectives = [main_goal] if main_goal else []
        quests = list((await self.session.execute(select(Quest).where(
            Quest.campaign_id == campaign_id,
            Quest.state.in_(list(ACTIONABLE_QUEST_STATES)),
        ).order_by(Quest.optional, Quest.sort_order))).scalars())
        objectives.extend(_text(q.task or q.name) for q in quests if _text(q.task or q.name))
        return list(dict.fromkeys(objectives))[:MAX_OBJECTIVES]

    async def _pressures(
        self, campaign_id: str, scene, prep: dict, game_time: int,
    ) -> tuple[list[str], list[str], str]:
        threats = list((await self.session.execute(select(Threat).where(
            Threat.campaign_id == campaign_id,
            Threat.status == "active",
        ).order_by(Threat.scheduled_game_time, Threat.progress.desc()))).scalars())
        immediate_ids = set(scene.immediate_threat_ids or [])
        immediate: list[str] = []
        clocks: list[str] = []
        for threat in threats:
            if threat.id in immediate_ids or f"threat:{threat.id}" in immediate_ids:
                line = _text(threat.next_action or threat.goal or threat.name)
                if line:
                    immediate.append(line)
            if threat.scheduled_game_time:
                remaining = threat.scheduled_game_time - game_time
                if remaining >= 0 and (
                    threat.id in immediate_ids or remaining <= max(threat.tick_interval, 240)
                ):
                    clocks.append(f"{threat.name}: เหลือประมาณ {remaining} นาทีในโลก")
        current = _text(prep.get("current_activity"))
        if current:
            immediate.insert(0, current)
        deadlines = (await MainStoryService(self.session).get(campaign_id)).get("deadlines", [])
        for deadline in deadlines[:4]:
            at = int(deadline.get("at_minute") or 0)
            if at >= game_time:
                clocks.append(f"{_text(deadline.get('what'))}: เหลือ {at - game_time} นาทีในโลก")
        events = list((await self.session.execute(select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.campaign_id == campaign_id,
            ScheduledWorldEvent.resolved.is_(False),
            ScheduledWorldEvent.perceivable.is_(True),
        ).order_by(ScheduledWorldEvent.due_game_time))).scalars())
        for event in events[:3]:
            clocks.append(
                f"{_text(event.payload.get('summary') or event.kind)}: "
                f"อีก {max(0, event.due_game_time - game_time)} นาทีในโลก"
            )
        immediate = list(dict.fromkeys(x for x in immediate if x))[:MAX_PRESSURES]
        clocks = list(dict.fromkeys(x for x in clocks if x))[:MAX_PRESSURES]
        stakes = clocks[0] if clocks else (immediate[-1] if immediate else "")
        return immediate, clocks, stakes

    async def _events(self, campaign_id: str, session_id: str) -> tuple[list[str], list[str]]:
        events = await EventService(self.session).list_visible_events(
            campaign_id=campaign_id,
            allowed_visibilities=[Visibility.PUBLIC, Visibility.PARTY],
        )
        summaries = [
            _text(e.payload.get("summary"))
            for e in events
            if isinstance(e.payload, dict) and _text(e.payload.get("summary"))
        ]
        recent = summaries[-MAX_EVENTS:]
        prior = [
            _text(e.payload.get("summary"))
            for e in events
            if e.session_id != session_id
            and isinstance(e.payload, dict)
            and _text(e.payload.get("summary"))
        ][-MAX_EVENTS:]
        return recent, prior
