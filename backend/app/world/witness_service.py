"""Witness resolution (§11): who PERCEIVES an event, and who can IDENTIFY the actor.

These are separate facts. A guard across a lit courtyard sees a brawl AND recognizes
the brawler; a guard in the next room only HEARS it (perceives, cannot identify); a
disguised, invisible, or well-concealed actor may be perceived without being
identified at all — producing an unattributed (open) crime.

The resolver models the spec's factors — presence, hearing, lighting, concealment,
disguise, invisibility, consciousness, public/private space, and nearby connected
locations — as explicit conditions rather than simulating geometry, so the outcome is
deterministic and testable. Callers supply the conditions they know about; the
default is a plainly public, lit, quiet act by an undisguised actor.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import entity_ref
from app.models.character import Character
from app.models.npc import NPC
from app.models.world_graph import LocationConnection


@dataclass
class WitnessOutcome:
    entity_ref: str        # "npc:<id>" | "character:<id>"
    via: str               # "sight" | "hearing"
    perceived: bool        # knows the event happened
    identified: bool       # can name the actor


@dataclass
class WitnessResolution:
    actor_ref: str | None
    witnesses: list[WitnessOutcome] = field(default_factory=list)

    @property
    def perceivers(self) -> list[str]:
        return [w.entity_ref for w in self.witnesses if w.perceived]

    @property
    def identifiers(self) -> list[str]:
        return [w.entity_ref for w in self.witnesses if w.identified]

    @property
    def any_perceived(self) -> bool:
        return any(w.perceived for w in self.witnesses)

    @property
    def any_identified(self) -> bool:
        return any(w.identified for w in self.witnesses)

    @property
    def perpetrator_ref(self) -> str | None:
        """The actor's identity is attributable only if SOMEONE identified them —
        the core mechanism behind an unattributed crime."""
        return self.actor_ref if self.any_identified else None


class WitnessService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(
        self, *, campaign_id: str, location_id: str, actor_ref: str | None = None,
        public: bool = True, loud: bool = False, lit: bool = True,
        actor_disguised: bool = False, actor_invisible: bool = False,
        actor_concealed: bool = False,
    ) -> WitnessResolution:
        # Can those present perceive the event at all? A concealed act in a private,
        # quiet place goes unnoticed (a pickpocket's sleight of hand); a public or loud
        # one is perceived regardless of concealment.
        event_perceptible = public or loud or not actor_concealed
        # Can the actor be tied to the event? Disguise, invisibility, concealment, and
        # darkness each break attribution even when the event itself is perceived.
        attributable = not (actor_disguised or actor_invisible or actor_concealed) and lit

        resolution = WitnessResolution(actor_ref=actor_ref)
        for ref in await self._present(campaign_id, location_id, exclude_ref=actor_ref):
            resolution.witnesses.append(WitnessOutcome(
                entity_ref=ref, via="sight",
                perceived=event_perceptible,
                identified=event_perceptible and attributable,
            ))
        if loud:
            for ref in await self._adjacent_present(
                campaign_id, location_id, exclude_ref=actor_ref
            ):
                # Heard through a wall or across the street: knows something happened,
                # cannot see who did it.
                resolution.witnesses.append(WitnessOutcome(
                    entity_ref=ref, via="hearing", perceived=True, identified=False,
                ))
        return resolution

    async def _present(
        self, campaign_id: str, location_id: str, *, exclude_ref: str | None,
    ) -> list[str]:
        """Conscious NPCs (not dead) + living characters AT this location, minus the actor."""
        refs: list[str] = []
        npcs = (await self.session.execute(select(NPC).where(
            NPC.campaign_id == campaign_id,
            NPC.current_location_id == location_id,
            NPC.physical_state != "dead",
        ))).scalars()
        for npc in npcs:
            ref = entity_ref("npc", npc.id)
            if ref != exclude_ref:
                refs.append(ref)
        chars = (await self.session.execute(select(Character).where(
            Character.campaign_id == campaign_id,
            Character.location_id == location_id,
        ))).scalars()
        for char in chars:
            if char.dead:
                continue
            ref = entity_ref("character", char.id)
            if ref != exclude_ref:
                refs.append(ref)
        return refs

    async def _adjacent_present(
        self, campaign_id: str, location_id: str, *, exclude_ref: str | None,
    ) -> list[str]:
        neighbours = (await self.session.execute(
            select(LocationConnection.to_location_id).where(
                LocationConnection.campaign_id == campaign_id,
                LocationConnection.from_location_id == location_id,
            )
        )).scalars()
        refs: list[str] = []
        for neighbour_id in dict.fromkeys(neighbours):  # dedupe, preserve order
            refs.extend(
                await self._present(campaign_id, neighbour_id, exclude_ref=exclude_ref)
            )
        return refs
