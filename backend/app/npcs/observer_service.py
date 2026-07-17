"""ObserverService — who NOTICES a world effect, and who SEES THROUGH it.

An illusion that nobody perceives is just a row in a table. This service decides,
deterministically, which NPCs are in a position to perceive a world effect and what
that does to them. It is the difference between "Neneko casts an illusion" and "the
illusion changes what the innkeeper is doing".

Two DIFFERENT questions, deliberately kept apart — conflating them is what makes
illusions feel either useless or unbeatable:

  NOTICE      — can this NPC perceive the effect at all? Decided by co-location and
                whether the effect's form (image/sound) is one they could catch. An
                NPC who notices REACTS TO IT AS REAL. This is not a check to pass;
                believing your own eyes is the default.
  SEE THROUGH  — does the NPC work out it is fake? Per SRD this needs a deliberate
                INT (Investigation) check against the caster's spell save DC, i.e. a
                REASON to doubt: physical interaction, or a goal that makes them go
                look. It never happens automatically, and it is rolled by the dice
                engine like any other check.

DELIBERATE LIMITATION: NPCs in this schema have no ability scores (see models/npc.py
— personality, goals, attitudes, location, availability, physical state; no INT/WIS).
So an NPC's investigation modifier cannot be derived from stats and uses
NPC_BASELINE_INVESTIGATION, adjusted by what the model DOES carry: an NPC whose goals
concern the disturbance is more likely to look closely. Giving NPCs real stats is the
right long-term fix and would slot in here without changing callers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import entity_ref
from app.core.logging import get_logger
from app.models.npc import NPC
from app.models.progression import ActiveEffect

log = get_logger(__name__)

# An ordinary NPC's INT(Investigation) modifier, standing in for ability scores the
# NPC model does not have. +0 = an unremarkable person, which is the honest default.
NPC_BASELINE_INVESTIGATION = 0
# An NPC whose goals are about the thing making noise looks harder at it.
INTERESTED_BONUS = 2
# Physical states that take an NPC out of the scene entirely.
_INSENSIBLE = {"dead", "gravely_wounded"}


@dataclass
class Observation:
    """One NPC's relationship to one world effect."""
    npc_id: str
    npc_name: str
    noticed: bool
    reason: str                       # why they did/didn't notice
    saw_through: bool = False
    check: dict | None = None         # the investigation roll, when one happened

    def as_dict(self) -> dict:
        return {"npc_id": self.npc_id, "npc_name": self.npc_name,
                "noticed": self.noticed, "reason": self.reason,
                "saw_through": self.saw_through, "check": self.check}


@dataclass
class EffectObservations:
    effect_id: str
    effect_name: str
    observations: list[Observation] = field(default_factory=list)

    @property
    def noticed_by(self) -> list[Observation]:
        return [o for o in self.observations if o.noticed]


class ObserverService:
    def __init__(self, session: AsyncSession, dice=None) -> None:
        self.session = session
        self.dice = dice

    async def candidates_at(self, *, campaign_id: str,
                            location_id: str | None) -> list[NPC]:
        """NPCs who could perceive something at `location_id`.

        Co-location IS the distance model here: an NPC elsewhere cannot see a 5-foot
        illusion in the tavern. Without a finer positional model this is the honest
        line to draw — and it is the line that keeps a distant NPC from reacting.
        """
        if not location_id:
            return []
        rows = list((await self.session.execute(select(NPC).where(
            NPC.campaign_id == campaign_id,
            NPC.current_location_id == location_id,
        ))).scalars())
        return [n for n in rows
                if n.available and (n.physical_state or "healthy") not in _INSENSIBLE]

    async def observe(
        self, *, campaign_id: str, effect: ActiveEffect,
        exclude_refs: list[str] | None = None,
    ) -> EffectObservations:
        """Who notices this world effect. Records nothing and rolls nothing — noticing
        is not a check."""
        data = effect.data or {}
        modes = data.get("modes") or []
        out = EffectObservations(effect_id=effect.id, effect_name=effect.name)
        excluded = set(exclude_refs or [])
        for npc in await self.candidates_at(campaign_id=campaign_id,
                                            location_id=effect.location_id):
            if entity_ref("npc", npc.id) in excluded:
                continue
            if not modes:
                out.observations.append(Observation(
                    npc_id=npc.id, npc_name=npc.name, noticed=False,
                    reason="effect has no perceivable form"))
                continue
            out.observations.append(Observation(
                npc_id=npc.id, npc_name=npc.name, noticed=True,
                reason=f"present at the location and the effect has a "
                       f"{'/'.join(modes)} form"))
        log.info("world effect observed",
                 extra={"effect_id": effect.id, "campaign_id": campaign_id,
                        "location_id": effect.location_id,
                        "candidates": len(out.observations),
                        "noticed": len(out.noticed_by)})
        return out

    async def investigate(
        self, *, campaign_id: str, effect: ActiveEffect, npc: NPC, dc: int,
    ) -> Observation:
        """A deliberate attempt by `npc` to work out the effect is fake.

        Only called when something GIVES them a reason. The roll is the dice engine's;
        on success the effect records who saw through it, so later turns know this NPC
        is no longer fooled.
        """
        if self.dice is None:
            raise ValueError("investigating an illusion requires the dice engine")
        modifier = NPC_BASELINE_INVESTIGATION
        if self._interested_in(npc, effect):
            modifier += INTERESTED_BONUS
        roll = self.dice.resolve_ability_check(
            modifier=modifier, dc=dc, ability="int", skill="investigation")
        saw_through = roll.outcome == "success"
        if saw_through:
            data = dict(effect.data or {})
            discovered = list(data.get("discovered_by") or [])
            ref = entity_ref("npc", npc.id)
            if ref not in discovered:
                discovered.append(ref)
            data["discovered_by"] = discovered
            data["investigated"] = True
            effect.data = data
        log.info("illusion investigated",
                 extra={"effect_id": effect.id, "npc_id": npc.id, "dc": dc,
                        "total": roll.total, "saw_through": saw_through})
        return Observation(
            npc_id=npc.id, npc_name=npc.name, noticed=True,
            reason="investigated the effect deliberately",
            saw_through=saw_through, check=roll.as_dict(),
        )

    @staticmethod
    def _interested_in(npc: NPC, effect: ActiveEffect) -> bool:
        """Does this NPC have a stated reason to look closely? Uses the goals the NPC
        model actually carries rather than inventing a motive."""
        text = " ".join(npc.goals or []).lower()
        data = effect.data or {}
        subject = f"{data.get('description') or ''} {data.get('category') or ''}".lower()
        if not text or not subject.strip():
            return False
        words = {w for w in subject.split() if len(w) > 3}
        return any(w in text for w in words)

    @staticmethod
    def is_fooled(effect: ActiveEffect, npc_id: str) -> bool:
        """Whether `npc` still believes the effect. Later turns read this, which is
        how an illusion keeps working (or stops) across the scene."""
        discovered = set((effect.data or {}).get("discovered_by") or [])
        return entity_ref("npc", npc_id) not in discovered
