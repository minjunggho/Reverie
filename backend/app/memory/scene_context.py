"""SceneContextBuilder — bounded, authorized canonical scene context.

The antidote to "เจ้าเห็นอะไรข้างนอก?": the DM already knows the world. This retrieves
only what a task needs (location sensory detail, exits, parent geography, present
cast, local state, active threats, allowed clues, recent events) and enforces
visibility — DM secrets never enter a player-facing block. Deep lore stays out
unless it's local + authorized.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities import SceneEntityDirectory
from app.models.campaign import Campaign
from app.models.enums import EventType, Visibility
from app.models.location import Location
from app.models.world import Threat
from app.models.world_graph import CampaignCanonRecord
from app.services.events import EventService
from app.world.graph_service import WorldGraphService


@dataclass
class SceneContext:
    location_name: str = "-"
    location_obvious: str = ""
    parent_path: str = ""
    weather: str = ""
    current_activity: str = ""
    exits: list[str] = field(default_factory=list)          # "front door → Bellmaker Street"
    present_pcs: list[str] = field(default_factory=list)
    present_npcs: list[str] = field(default_factory=list)
    local_canon: list[str] = field(default_factory=list)    # PUBLIC/PARTY facts only
    allowed_clues: list[str] = field(default_factory=list)
    active_pressure: list[str] = field(default_factory=list)  # DM-authorized threat lines
    recent_events: list[str] = field(default_factory=list)
    game_time: int = 0

    def location_block(self) -> str:
        """Player-safe canonical scene the narrator frames FROM (never invents)."""
        lines = [f"LOCATION: {self.location_name}"]
        if self.parent_path:
            lines.append(f"AREA: {self.parent_path}")
        if self.location_obvious:
            lines.append(f"OBVIOUS: {self.location_obvious}")
        if self.weather:
            lines.append(f"CONDITION: {self.weather}")
        if self.current_activity:
            lines.append(f"ACTIVITY: {self.current_activity}")
        if self.exits:
            lines.append("EXITS:\n" + "\n".join(f"- {e}" for e in self.exits))
        if self.present_npcs:
            lines.append("PRESENT_NPCS: " + ", ".join(self.present_npcs))
        if self.local_canon:
            lines.append("LOCAL_CANON:\n" + "\n".join(f"- {c}" for c in self.local_canon))
        return "\n".join(lines)

    def pressure_block(self) -> str:
        """DM-only planning context — active fronts + recent events. Never sent to a
        player-safe narration prompt unmodified; used by scene framing/planning."""
        lines = []
        if self.active_pressure:
            lines.append("ACTIVE_PRESSURE:\n" + "\n".join(f"- {p}" for p in self.active_pressure))
        if self.recent_events:
            lines.append("RECENT:\n" + "\n".join(f"- {e}" for e in self.recent_events))
        return "\n".join(lines)


class SceneContextBuilder:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build(
        self, *, campaign_id: str, scene, actor_character_id: str | None,
        include_pressure: bool = False,
    ) -> SceneContext:
        ctx = SceneContext()
        campaign = await self.session.get(Campaign, campaign_id)
        ctx.game_time = campaign.current_game_time if campaign else 0

        location_id = scene.location_id if scene is not None else None
        if location_id:
            loc = await self.session.get(Location, location_id)
            if loc is not None:
                ctx.location_name = loc.name
                ctx.location_obvious = loc.description_obvious
                ctx.weather = loc.weather
                ctx.current_activity = loc.current_activity
                ctx.parent_path = await self._parent_path(loc)
                for e in await WorldGraphService(self.session).exits(location_id):
                    if not e.obvious:
                        continue
                    dest = await self.session.get(Location, e.to_location_id)
                    label = e.label or (e.direction or "ทางออก")
                    ctx.exits.append(f"{label} → {dest.name if dest else '?'}"
                                     + (f" ({e.travel_minutes} นาที)" if e.travel_minutes else "")
                                     + ("" if e.access_state == "open" else f" [{e.access_state}]"))
                ctx.local_canon = await self._local_canon(campaign_id, location_id)

        # Present cast from the directory (PC presence = co-location/participants).
        if scene is not None:
            directory = await SceneEntityDirectory(self.session).build(
                scene, actor_character_id=actor_character_id, campaign_id=campaign_id)
            ctx.present_pcs = [e.canonical_name for e in directory.present_player_characters]
            ctx.present_npcs = [e.canonical_name for e in directory.present_npcs]
            ctx.allowed_clues = list(scene.allowed_clues or [])

        if include_pressure:
            ctx.active_pressure = await self._active_pressure(campaign_id)
            ctx.recent_events = await self._recent_events(campaign_id, scene)
        return ctx

    async def _parent_path(self, loc: Location) -> str:
        names, cur, guard = [], loc, 0
        while cur.parent_id and guard < 6:
            parent = await self.session.get(Location, cur.parent_id)
            if parent is None:
                break
            names.append(parent.name)
            cur, guard = parent, guard + 1
        return " · ".join(reversed(names))

    async def _local_canon(self, campaign_id: str, location_id: str) -> list[str]:
        rows = (await self.session.execute(
            select(CampaignCanonRecord).where(
                CampaignCanonRecord.campaign_id == campaign_id,
                CampaignCanonRecord.active.is_(True),
                CampaignCanonRecord.visibility.in_([Visibility.PUBLIC.value, Visibility.PARTY.value]),
            ).order_by(CampaignCanonRecord.importance.desc())
        )).scalars().all()
        out = []
        for r in rows:
            if r.scope_type in (None, "location") and (r.scope_id in (None, location_id)):
                out.append(r.fact)
        return out[:6]

    async def _active_pressure(self, campaign_id: str) -> list[str]:
        threats = (await self.session.execute(
            select(Threat).where(Threat.campaign_id == campaign_id, Threat.status == "active")
            .order_by(Threat.progress.desc())
        )).scalars().all()
        return [f"{t.name}: {t.next_action or t.goal} (progress {t.progress})" for t in threats[:5]]

    async def _recent_events(self, campaign_id: str, scene) -> list[str]:
        events = await EventService(self.session).list_visible_events(
            campaign_id=campaign_id,
            allowed_visibilities=[Visibility.PUBLIC, Visibility.PARTY],
            session_id=scene.session_id if scene is not None else None,
        )
        summaries = [e.payload.get("summary") for e in events
                     if isinstance(e.payload, dict) and e.payload.get("summary")]
        return summaries[-5:]
