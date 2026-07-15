"""ConsequenceService — the typed, validated command layer for persistent world
consequences (§11–13).

Every command here:

- validates its own arguments (schema) and rejects nonsense;
- resolves its target and refuses cross-campaign references (campaign isolation +
  target validation);
- is idempotent where it creates rows — a source event id or an idempotency key
  dedupes, so the SAME triggering action can never double-apply;
- records exactly one canonical ``Event`` through the shared ``EventService`` (the only
  sanctioned event sink), so the state change and its record commit together.

This service does NOT invent authoritative mechanics: HP/damage, currency, items, and
combat remain owned by the dice, economy, inventory, and combat services and are reused
— never re-implemented — from here. What lives here is the persistent CONSEQUENCE +
KNOWLEDGE layer the world lacked: crime, reputation, factions, quests, rumors,
injuries, location/access state, and scheduled follow-ups. Memory/relationship changes
delegate to the existing :class:`NPCMemoryService`.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.ids import entity_ref
from app.models.consequences import (
    QUEST_STATES,
    REPUTATION_SCOPES,
    RUMOR_LADDER,
    CrimeRecord,
    Faction,
    Quest,
    Reputation,
    Rumor,
)
from app.models.enums import EventType, Visibility
from app.models.location import Location
from app.models.npc import NPC
from app.models.world import ScheduledWorldEvent, Threat
from app.models.world_graph import LocationConnection
from app.npcs.memory_service import NPCMemoryService
from app.services.events import EventService
from app.world.witness_service import WitnessResolution

# Narrative injury severity → (physical_state, still-available?). NOT hit points.
_INJURY: dict[str, tuple[str, bool]] = {
    "hurt": ("hurt", True),
    "wounded": ("wounded", False),
    "gravely_wounded": ("gravely_wounded", False),
    "dead": ("dead", False),
}

_ACCESS_STATES = ("open", "locked", "blocked", "hidden")


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


class ConsequenceService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        campaign_id: str,
        session_id: str | None = None,
        scene_id: str | None = None,
        actor_entity: str | None = None,
    ) -> None:
        self.session = session
        self.events = EventService(session)
        self.campaign_id = campaign_id
        self.session_id = session_id
        self.scene_id = scene_id
        self.actor_entity = actor_entity

    async def _record(self, event_type: EventType, **kw: Any):
        kw.setdefault("actor_entity", self.actor_entity)
        return await self.events.record(
            campaign_id=self.campaign_id, session_id=self.session_id,
            scene_id=self.scene_id, event_type=event_type, **kw,
        )

    # --- NPC condition ------------------------------------------------------
    async def injure_npc(self, *, npc_id: str, severity: str = "wounded", reason: str = ""):
        """Set an NPC's durable physical condition + availability. Persists across
        sessions and restart. This is narrative condition, never authoritative HP."""
        if severity not in _INJURY:
            raise ValidationError(
                f"unknown injury severity {severity!r} (allowed: {sorted(_INJURY)})")
        npc = await self._own_npc(npc_id)
        before = npc.physical_state
        state, available = _INJURY[severity]
        npc.physical_state = state
        npc.available = available
        await self.session.flush()
        await self._record(
            EventType.NPC_INJURED,
            target_entities=[entity_ref("npc", npc_id)],
            location_id=npc.current_location_id, visibility=Visibility.PARTY,
            mechanical_changes={"physical_state": {"from": before, "to": state},
                                "available": available},
            payload={"reason": reason, "severity": severity}, narrative_significance=45,
        )
        return npc

    async def set_npc_available(self, *, npc_id: str, available: bool, reason: str = ""):
        """Open/close an NPC for interaction (an injured shopkeeper closes tomorrow)."""
        npc = await self._own_npc(npc_id)
        before = npc.available
        npc.available = bool(available)
        await self.session.flush()
        await self._record(
            EventType.NPC_STATE_CHANGED,
            target_entities=[entity_ref("npc", npc_id)],
            location_id=npc.current_location_id, visibility=Visibility.PARTY,
            mechanical_changes={"available": {"from": before, "to": npc.available}},
            payload={"reason": reason, "change": "availability"}, narrative_significance=20,
        )
        return npc

    async def move_npc(self, *, npc_id: str, to_location_id: str | None, reason: str = ""):
        npc = await self._own_npc(npc_id)
        if to_location_id is not None:
            await self._own_location(to_location_id)
        before = npc.current_location_id
        npc.current_location_id = to_location_id
        await self.session.flush()
        await self._record(
            EventType.NPC_STATE_CHANGED,
            target_entities=[entity_ref("npc", npc_id)], location_id=to_location_id,
            visibility=Visibility.PARTY,
            mechanical_changes={"location": {"from": before, "to": to_location_id}},
            payload={"reason": reason, "change": "moved"}, narrative_significance=20,
        )
        return npc

    async def change_emotion(self, *, npc_id: str, emotional_state: str, reason: str = ""):
        npc = await self._own_npc(npc_id)
        before = npc.emotional_state
        npc.emotional_state = emotional_state
        await self.session.flush()
        await self._record(
            EventType.NPC_STATE_CHANGED,
            target_entities=[entity_ref("npc", npc_id)], visibility=Visibility.DM_ONLY,
            payload={"emotional_state": {"from": before, "to": emotional_state},
                     "reason": reason}, narrative_significance=15,
        )
        return npc

    # --- location + access state -------------------------------------------
    async def change_access_state(
        self, *, from_location_id: str, to_location_id: str, state: str = "blocked",
        both_directions: bool = True, reason: str = "",
    ):
        """Change a travel edge's access state. A destroyed bridge set to ``blocked``
        stops routing through it — RouteService only traverses ``open`` edges — and the
        change persists, so navigation stays altered after a restart."""
        if state not in _ACCESS_STATES:
            raise ValidationError(
                f"unknown access_state {state!r} (allowed: {list(_ACCESS_STATES)})")
        edges = await self._edges(from_location_id, to_location_id, both_directions)
        if not edges:
            raise NotFoundError(
                f"no connection between {from_location_id} and {to_location_id}")
        changes = []
        for edge in edges:
            changes.append({"edge": edge.id, "from": edge.access_state, "to": state})
            edge.access_state = state
        await self.session.flush()
        await self._record(
            EventType.ACCESS_STATE_CHANGED,
            target_entities=[entity_ref("location", to_location_id)],
            location_id=from_location_id, visibility=Visibility.PARTY,
            mechanical_changes={"edges": changes},
            payload={"reason": reason, "state": state,
                     "from_location_id": from_location_id,
                     "to_location_id": to_location_id}, narrative_significance=35,
        )
        return edges

    async def set_location_state(self, *, location_id: str, changes: dict[str, Any], reason: str = ""):
        """Merge durable changes into a location's ``state`` dict (damaged/destroyed,
        closed, prices/services changed)."""
        if not isinstance(changes, dict):
            raise ValidationError("set_location_state requires a dict of state changes")
        loc = await self._own_location(location_id)
        before = dict(loc.state or {})
        merged = {**before, **changes}
        loc.state = merged
        await self.session.flush()
        await self._record(
            EventType.LOCATION_STATE_CHANGED,
            target_entities=[entity_ref("location", location_id)],
            location_id=location_id, visibility=Visibility.PARTY,
            mechanical_changes={"state": {"from": before, "to": merged}},
            payload={"reason": reason}, narrative_significance=30,
        )
        return loc

    async def discover_route(
        self, *, from_location_id: str, to_location_id: str, both_directions: bool = True,
    ):
        edges = await self._edges(from_location_id, to_location_id, both_directions)
        if not edges:
            raise NotFoundError(
                f"no connection between {from_location_id} and {to_location_id}")
        for edge in edges:
            edge.discovery_state = "KNOWN"
        await self.session.flush()
        await self._record(
            EventType.ROUTE_DISCOVERED,
            target_entities=[entity_ref("location", to_location_id)],
            location_id=from_location_id, visibility=Visibility.PARTY,
            payload={"from_location_id": from_location_id,
                     "to_location_id": to_location_id}, narrative_significance=25,
        )
        return edges

    # --- quests -------------------------------------------------------------
    async def update_quest(
        self, *, key: str, name: str | None = None, state: str | None = None,
        progress: int | None = None, data: dict[str, Any] | None = None,
    ):
        """Upsert a quest by stable ``key``. State/progress/leads persist across sessions."""
        if not key:
            raise ValidationError("update_quest requires a key")
        if state is not None and state not in QUEST_STATES:
            raise ValidationError(
                f"unknown quest state {state!r} (allowed: {list(QUEST_STATES)})")
        quest = (await self.session.execute(select(Quest).where(
            Quest.campaign_id == self.campaign_id, Quest.key == key))).scalars().first()
        created = quest is None
        if quest is None:
            quest = Quest(campaign_id=self.campaign_id, key=key, name=name or key)
            self.session.add(quest)
        before_state = quest.state
        if name is not None:
            quest.name = name
        if state is not None:
            quest.state = state
        if progress is not None:
            quest.progress = _clamp(progress, 0, 100)
        if data:
            quest.data = {**(quest.data or {}), **data}
        await self.session.flush()
        await self._record(
            EventType.QUEST_STATE_CHANGED,
            target_entities=[f"quest:{quest.id}"], visibility=Visibility.PARTY,
            mechanical_changes={"state": {"from": None if created else before_state,
                                          "to": quest.state}, "progress": quest.progress},
            payload={"key": key, "name": quest.name}, narrative_significance=35,
        )
        return quest

    # --- crime + reputation -------------------------------------------------
    async def record_crime(
        self, *, crime_type: str, location_id: str | None = None, game_time: int = 0,
        victim_ref: str | None = None, witness_resolution: WitnessResolution | None = None,
        source_event_id: str | None = None,
    ):
        """Record an offence. Perceived/identified are derived from a witness
        resolution: with no identifying witness the perpetrator stays NULL (an
        unattributed, open crime). Idempotent per ``source_event_id``."""
        if not crime_type:
            raise ValidationError("record_crime requires a crime_type")
        if source_event_id is not None:
            existing = (await self.session.execute(select(CrimeRecord).where(
                CrimeRecord.campaign_id == self.campaign_id,
                CrimeRecord.source_event_id == source_event_id))).scalars().first()
            if existing is not None:
                return existing
        if location_id is not None:
            await self._own_location(location_id)

        perceived, identified, perpetrator_ref, witnesses = True, False, None, []
        if witness_resolution is not None:
            perceived = witness_resolution.any_perceived
            identified = witness_resolution.any_identified
            perpetrator_ref = witness_resolution.perpetrator_ref
            witnesses = witness_resolution.perceivers

        crime = CrimeRecord(
            campaign_id=self.campaign_id, crime_type=crime_type, victim_ref=victim_ref,
            perpetrator_ref=perpetrator_ref, location_id=location_id, game_time=game_time,
            perceived=perceived, identified=identified, reported=False, status="open",
            witnesses=witnesses, source_event_id=source_event_id,
        )
        self.session.add(crime)
        await self.session.flush()
        # An unperceived crime is DM-only — nobody in-world knows it happened yet.
        visibility = Visibility.PARTY if perceived else Visibility.DM_ONLY
        await self._record(
            EventType.CRIME_RECORDED,
            target_entities=[t for t in (victim_ref, perpetrator_ref) if t],
            location_id=location_id, witnesses=witnesses, visibility=visibility,
            mechanical_changes={"crime_id": crime.id, "perceived": perceived,
                                "identified": identified},
            payload={"crime_type": crime_type}, narrative_significance=55,
        )
        return crime

    async def discover_crime(self, *, crime_id: str, perpetrator_ref: str | None = None):
        """A previously-unperceived crime becomes known (a body is found; the till is
        short). Optionally attributes the actor now that evidence surfaced."""
        crime = await self.session.get(CrimeRecord, crime_id)
        if crime is None or crime.campaign_id != self.campaign_id:
            raise NotFoundError(f"crime {crime_id} not found")
        crime.perceived = True
        if perpetrator_ref is not None:
            crime.perpetrator_ref = perpetrator_ref
            crime.identified = True
        await self.session.flush()
        await self._record(
            EventType.CRIME_DISCOVERED,
            target_entities=[crime.perpetrator_ref] if crime.perpetrator_ref else [],
            location_id=crime.location_id, visibility=Visibility.PARTY,
            mechanical_changes={"crime_id": crime.id, "identified": crime.identified},
            payload={"crime_type": crime.crime_type}, narrative_significance=45,
        )
        return crime

    async def report_crime(self, *, crime_id: str, to_scope_ref: str | None = None):
        """Mark a crime formally reported (a merchant tells the watch in the evening)."""
        crime = await self.session.get(CrimeRecord, crime_id)
        if crime is None or crime.campaign_id != self.campaign_id:
            raise NotFoundError(f"crime {crime_id} not found")
        crime.reported = True
        crime.perceived = True
        if crime.status == "open":
            crime.status = "reported"
        await self.session.flush()
        await self._record(
            EventType.CRIME_RECORDED,
            location_id=crime.location_id, visibility=Visibility.DM_ONLY,
            mechanical_changes={"crime_id": crime.id, "reported": True},
            payload={"crime_type": crime.crime_type, "to": to_scope_ref,
                     "change": "reported"}, narrative_significance=30,
        )
        return crime

    async def change_reputation(
        self, *, subject_ref: str, scope: str, amount: int = 0,
        scope_ref: str | None = None, wanted: bool | None = None, reason: str = "",
    ):
        """Adjust a subject's standing within one social scope. DM-scoped: players do
        not automatically learn how far word has spread."""
        if scope not in REPUTATION_SCOPES:
            raise ValidationError(
                f"unknown reputation scope {scope!r} (allowed: {list(REPUTATION_SCOPES)})")
        rep = (await self.session.execute(select(Reputation).where(
            Reputation.campaign_id == self.campaign_id,
            Reputation.subject_ref == subject_ref, Reputation.scope == scope,
            Reputation.scope_ref == scope_ref))).scalars().first()
        if rep is None:
            rep = Reputation(campaign_id=self.campaign_id, subject_ref=subject_ref,
                             scope=scope, scope_ref=scope_ref)
            self.session.add(rep)
        before = rep.value
        rep.value = _clamp(before + int(amount), -100, 100)
        if wanted is not None:
            rep.wanted = bool(wanted)
        await self.session.flush()
        await self._record(
            EventType.REPUTATION_CHANGED,
            target_entities=[subject_ref], visibility=Visibility.DM_ONLY,
            mechanical_changes={"value": {"from": before, "to": rep.value},
                                "wanted": rep.wanted},
            payload={"scope": scope, "scope_ref": scope_ref, "reason": reason},
            narrative_significance=30,
        )
        return rep

    # --- rumors -------------------------------------------------------------
    async def spread_rumor(
        self, *, content: str, origin_location_id: str | None = None, truth: bool = True,
        known_scope: str = "LOCAL", source_event_id: str | None = None,
    ):
        """Introduce a rumor at a starting scope. Idempotent per ``source_event_id``."""
        if not content:
            raise ValidationError("spread_rumor requires content")
        if known_scope not in RUMOR_LADDER:
            raise ValidationError(
                f"unknown rumor scope {known_scope!r} (allowed: {list(RUMOR_LADDER)})")
        if source_event_id is not None:
            existing = (await self.session.execute(select(Rumor).where(
                Rumor.campaign_id == self.campaign_id,
                Rumor.source_event_id == source_event_id))).scalars().first()
            if existing is not None:
                return existing
        rumor = Rumor(
            campaign_id=self.campaign_id, content=content, truth=truth,
            origin_location_id=origin_location_id, spread_stage=0,
            known_scope=known_scope, source_event_id=source_event_id,
        )
        self.session.add(rumor)
        await self.session.flush()
        await self._record(
            EventType.RUMOR_SPREAD, location_id=origin_location_id,
            visibility=Visibility.DM_ONLY,
            mechanical_changes={"rumor_id": rumor.id, "known_scope": known_scope,
                                "spread_stage": 0},
            payload={"content": content, "truth": truth}, narrative_significance=25,
        )
        return rumor

    async def widen_rumor(self, *, rumor_id: str):
        """Advance a rumor one rung up the reach ladder (LOCAL→…→POLITICAL)."""
        rumor = await self.session.get(Rumor, rumor_id)
        if rumor is None or rumor.campaign_id != self.campaign_id:
            raise NotFoundError(f"rumor {rumor_id} not found")
        before = rumor.known_scope
        try:
            idx = RUMOR_LADDER.index(rumor.known_scope)
        except ValueError:
            idx = 0
        if idx < len(RUMOR_LADDER) - 1:
            rumor.known_scope = RUMOR_LADDER[idx + 1]
            rumor.spread_stage += 1
        await self.session.flush()
        await self._record(
            EventType.RUMOR_SPREAD, location_id=rumor.origin_location_id,
            visibility=Visibility.DM_ONLY,
            mechanical_changes={"rumor_id": rumor.id,
                                "known_scope": {"from": before, "to": rumor.known_scope},
                                "spread_stage": rumor.spread_stage},
            payload={"content": rumor.content}, narrative_significance=20,
        )
        return rumor

    # --- factions + threats -------------------------------------------------
    async def create_faction(
        self, *, name: str, goal: str = "", leader_ref: str | None = None,
        progress: int = 0, disposition_to_party: int = 0, scheduled_game_time: int = 0,
        territory: list[str] | None = None, plans: str = "",
    ):
        faction = Faction(
            campaign_id=self.campaign_id, name=name, goal=goal, leader_ref=leader_ref,
            progress=_clamp(progress, 0, 100),
            disposition_to_party=_clamp(disposition_to_party, -100, 100),
            scheduled_game_time=scheduled_game_time, territory=list(territory or []),
            plans=plans,
        )
        self.session.add(faction)
        await self.session.flush()
        await self._record(
            EventType.FACTION_ADVANCED, target_entities=[f"faction:{faction.id}"],
            visibility=Visibility.DM_ONLY,
            payload={"name": name, "created": True}, narrative_significance=20,
        )
        return faction

    async def advance_faction(
        self, *, faction_id: str, progress_delta: int = 0, disposition_delta: int = 0,
        next_in_minutes: int | None = None, current_game_time: int = 0, note: str = "",
    ):
        """Advance a faction toward its goal and (optionally) reschedule its next beat.
        Fired from the world clock so a faction keeps moving on its own timeline."""
        faction = await self.session.get(Faction, faction_id)
        if faction is None or faction.campaign_id != self.campaign_id:
            raise NotFoundError(f"faction {faction_id} not found")
        before_progress = faction.progress
        before_disp = faction.disposition_to_party
        faction.progress = _clamp(before_progress + int(progress_delta), 0, 100)
        faction.disposition_to_party = _clamp(
            before_disp + int(disposition_delta), -100, 100)
        if faction.progress >= 100 and faction.status == "active":
            faction.status = "resolved"
        if next_in_minutes is not None:
            faction.scheduled_game_time = current_game_time + int(next_in_minutes)
        await self.session.flush()
        await self._record(
            EventType.FACTION_ADVANCED, target_entities=[f"faction:{faction.id}"],
            visibility=Visibility.DM_ONLY,
            mechanical_changes={"progress": {"from": before_progress, "to": faction.progress},
                                "disposition": {"from": before_disp,
                                                "to": faction.disposition_to_party}},
            payload={"name": faction.name, "note": note}, narrative_significance=30,
        )
        return faction

    async def update_threat(
        self, *, threat_id: str, progress_delta: int = 0, status: str | None = None,
        next_in_minutes: int | None = None, current_game_time: int = 0, note: str = "",
    ):
        threat = await self.session.get(Threat, threat_id)
        if threat is None or threat.campaign_id != self.campaign_id:
            raise NotFoundError(f"threat {threat_id} not found")
        before = threat.progress
        threat.progress = _clamp(before + int(progress_delta), 0, 100)
        if status is not None:
            threat.status = status
        elif threat.progress >= 100 and threat.status == "active":
            threat.status = "resolved"
        if next_in_minutes is not None:
            threat.scheduled_game_time = current_game_time + int(next_in_minutes)
        await self.session.flush()
        await self._record(
            EventType.THREAT_ADVANCED, actor_entity=f"threat:{threat.id}",
            visibility=Visibility.DM_ONLY,
            mechanical_changes={"progress": {"from": before, "to": threat.progress}},
            payload={"name": threat.name, "note": note}, narrative_significance=25,
        )
        return threat

    # --- scheduled follow-ups ----------------------------------------------
    async def schedule_response(
        self, *, kind: str, due_game_time: int, payload: dict[str, Any] | None = None,
        perceivable: bool = False, idempotency_key: str | None = None,
    ):
        """Persist a delayed consequence to fire when in-world time reaches
        ``due_game_time``. The world clock fires it EXACTLY ONCE. Idempotent per
        ``idempotency_key`` so the same triggering action never schedules a duplicate."""
        body = dict(payload or {})
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key
            existing = (await self.session.execute(select(ScheduledWorldEvent).where(
                ScheduledWorldEvent.campaign_id == self.campaign_id,
                ScheduledWorldEvent.kind == kind))).scalars().all()
            for ev in existing:
                if (ev.payload or {}).get("idempotency_key") == idempotency_key:
                    return ev
        ev = ScheduledWorldEvent(
            campaign_id=self.campaign_id, due_game_time=due_game_time, kind=kind,
            payload=body, perceivable=perceivable,
        )
        self.session.add(ev)
        await self.session.flush()
        await self._record(
            EventType.CONSEQUENCE_SCHEDULED, visibility=Visibility.DM_ONLY,
            mechanical_changes={"scheduled_event_id": ev.id, "kind": kind,
                                "due_game_time": due_game_time},
            payload={"kind": kind, **body}, narrative_significance=15,
        )
        return ev

    # --- memory (delegated to the existing NPC memory system) --------------
    async def create_memory(
        self, *, npc_id: str, subject_ref: str, event_id: str, memory_type: str,
        summary: str, importance: int = 40, valence: int = 0,
        location_id: str | None = None, game_time: int = 0,
        relationship_deltas: dict[str, int] | None = None,
    ):
        """Give an NPC a durable episodic memory of what a character did — reusing the
        existing NPCMemoryService (idempotent per ``event_id``), which also accumulates
        the multi-dimensional relationship."""
        await self._own_npc(npc_id)
        return await NPCMemoryService(self.session).record_typed_memory(
            npc_id=npc_id, subject_ref=subject_ref, event_id=event_id,
            memory_type=memory_type, summary=summary, importance=importance,
            valence=valence, source_ref=subject_ref, location_id=location_id,
            game_time=game_time, relationship_deltas=relationship_deltas,
        )

    # --- helpers ------------------------------------------------------------
    async def _own_npc(self, npc_id: str) -> NPC:
        npc = await self.session.get(NPC, npc_id)
        if npc is None or npc.campaign_id != self.campaign_id:
            raise NotFoundError(f"npc {npc_id} is not in campaign {self.campaign_id}")
        return npc

    async def _own_location(self, location_id: str) -> Location:
        loc = await self.session.get(Location, location_id)
        if loc is None or loc.campaign_id != self.campaign_id:
            raise ValidationError(
                f"location {location_id} is not in campaign {self.campaign_id}")
        return loc

    async def _edges(
        self, a: str, b: str, both: bool,
    ) -> list[LocationConnection]:
        await self._own_location(a)
        await self._own_location(b)
        pairs = [(a, b)] + ([(b, a)] if both else [])
        edges: list[LocationConnection] = []
        for src, dst in pairs:
            rows = (await self.session.execute(select(LocationConnection).where(
                LocationConnection.campaign_id == self.campaign_id,
                LocationConnection.from_location_id == src,
                LocationConnection.to_location_id == dst))).scalars().all()
            edges.extend(rows)
        return edges
