"""SituationReader — DC factors derived from authoritative state.

The half of situational difficulty the model has no say in. Everything here is read
from committed records: what an NPC has actually earned toward THIS character, what
mood and condition they are in, what the weather is, how tense the scene is, and what
world effects are standing in the room.

This is where "the world remembers" becomes mechanical rather than decorative. An
innkeeper who trusts you is measurably easier to persuade, because the trust is a
number that a previous scene put there — not because the narrator felt generous.

Every reader below FAILS SOFT: missing state yields no factor, never a guess. A DC
that silently drifts because a lookup broke would be worse than a flat one.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import parse_entity_ref
from app.core.logging import get_logger
from app.models.npc import NPC
from app.models.npc_epistemic import NPCRelationship
from app.tabletop.adjudication.difficulty import DCFactor

log = get_logger(__name__)

_SOCIAL = frozenset({"persuasion", "deception", "intimidation", "performance",
                     "insight"})
_PERSUASIVE = frozenset({"persuasion", "performance"})
_PERCEPTIVE = frozenset({"perception", "investigation", "survival"})

# Relationship dimensions run -100..100 and accumulate a few points per interaction
# (see NPCMemoryService). These thresholds mirror the stance ladder in
# `_derive_stance`, so the DC agrees with the stance the table is being shown.
_TRUST_FRIENDLY, _TRUST_LOYAL = 15, 25
_SUSPICION_WARY = 20
_ANGER_GUARDED, _ANGER_HOSTILE = 10, 20
_FEAR_AFRAID = 20


class SituationReader:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def factors(
        self, *, campaign_id: str, actor_ref: str | None, skill: str | None,
        target_ref: str | None = None, scene=None,
        location_id: str | None = None,
    ) -> list[DCFactor]:
        """Every engine-derived factor for this check, in one call."""
        out: list[DCFactor] = []
        try:
            if target_ref and actor_ref:
                out += await self._relationship_factors(
                    target_ref=target_ref, actor_ref=actor_ref, skill=skill)
                out += await self._npc_condition_factors(
                    target_ref=target_ref, skill=skill)
            out += await self._world_effect_factors(
                campaign_id=campaign_id, skill=skill,
                scene_id=getattr(scene, "id", None), location_id=location_id)
            out += await self._environment_factors(
                location_id=location_id, skill=skill)
        except Exception as exc:  # noqa: BLE001 — a DC must never break the turn
            log.warning("situational factor derivation failed; using the band alone: %s",
                        exc)
            return []
        return out

    # --- what this NPC feels about THIS character --------------------------------
    async def _relationship_factors(self, *, target_ref: str, actor_ref: str,
                                    skill: str | None) -> list[DCFactor]:
        """Earned feeling, and only for the checks it could plausibly touch. A guard
        who fears you is not thereby easier to lie to."""
        if not skill or skill not in _SOCIAL:
            return []
        kind, npc_id = parse_entity_ref(target_ref)
        if kind != "npc" or not npc_id:
            return []
        rel = (await self.session.execute(select(NPCRelationship).where(
            NPCRelationship.npc_id == npc_id,
            NPCRelationship.entity_ref == actor_ref,
        ))).scalars().first()
        if rel is None:
            return []

        out: list[DCFactor] = []
        trust, affection = int(rel.trust or 0), int(rel.affection or 0)
        if skill in _PERSUASIVE:
            if trust >= _TRUST_LOYAL or (trust >= _TRUST_FRIENDLY
                                         and affection >= _TRUST_FRIENDLY):
                out.append(DCFactor("relationship_loyal", -3,
                                    "เขาไว้ใจเจ้ามาก", "engine"))
            elif trust >= _TRUST_FRIENDLY or affection >= _TRUST_FRIENDLY:
                out.append(DCFactor("relationship_friendly", -2,
                                    "เขาเป็นมิตรกับเจ้า", "engine"))
        if skill == "deception" and int(rel.suspicion or 0) >= _SUSPICION_WARY:
            out.append(DCFactor("relationship_suspicious", +3,
                                "เขาระแวงเจ้า", "engine"))
        anger = int(rel.anger or 0)
        if skill in _PERSUASIVE and anger >= _ANGER_HOSTILE:
            out.append(DCFactor("relationship_hostile", +3, "เขาโกรธเจ้า", "engine"))
        elif skill in _PERSUASIVE and anger >= _ANGER_GUARDED:
            out.append(DCFactor("relationship_guarded", +2,
                                "เขายังไม่พอใจเจ้า", "engine"))
        if skill == "intimidation" and int(rel.fear or 0) >= _FEAR_AFRAID:
            out.append(DCFactor("relationship_afraid", -3, "เขากลัวเจ้าอยู่แล้ว",
                                "engine"))
        return out

    # --- the NPC's own condition ---------------------------------------------------
    async def _npc_condition_factors(self, *, target_ref: str,
                                     skill: str | None) -> list[DCFactor]:
        if not skill or skill not in _SOCIAL:
            return []
        kind, npc_id = parse_entity_ref(target_ref)
        if kind != "npc" or not npc_id:
            return []
        npc = await self.session.get(NPC, npc_id)
        if npc is None:
            return []
        out: list[DCFactor] = []
        mood = (npc.emotional_state or "").strip().lower()
        if skill in _PERSUASIVE and mood in ("angry", "furious", "โกรธ"):
            out.append(DCFactor("npc_angry", +2, "เขากำลังโมโห", "engine"))
        elif skill in _PERSUASIVE and mood in ("happy", "cheerful", "content",
                                               "ดีใจ"):
            out.append(DCFactor("npc_content", -2, "เขากำลังอารมณ์ดี", "engine"))
        if skill == "intimidation" and mood in ("afraid", "scared", "nervous",
                                                "กลัว"):
            out.append(DCFactor("npc_already_afraid", -2, "เขากำลังหวาดหวั่น",
                                "engine"))
        # A hurt NPC is easier to cow, and harder to reason with calmly.
        if (npc.physical_state or "healthy") in ("wounded", "gravely_wounded"):
            if skill == "intimidation":
                out.append(DCFactor("npc_wounded", -2, "เขาบาดเจ็บอยู่", "engine"))
        return out

    # --- what is standing in the room ----------------------------------------------
    async def _world_effect_factors(self, *, campaign_id: str, skill: str | None,
                                    scene_id: str | None,
                                    location_id: str | None) -> list[DCFactor]:
        """Live world effects with mechanical weight. A fog cloud is the case that
        makes this real: it obscures, so spotting gets harder and hiding gets easier —
        and it does so because a spell put it there, not because the DM said so."""
        if not skill or not (scene_id or location_id):
            return []
        from app.tabletop.effects import EffectService

        effects = await EffectService(self.session).world_effects_in(
            campaign_id=campaign_id, scene_id=scene_id, location_id=location_id)
        out: list[DCFactor] = []
        for effect in effects:
            category = ((effect.data or {}).get("category") or "").lower()
            if category == "obscurement":
                if skill in _PERCEPTIVE:
                    out.append(DCFactor("obscured", +3, f"{effect.name}บดบัง",
                                        "engine"))
                elif skill == "stealth":
                    out.append(DCFactor("obscuring_cover", -3,
                                        f"{effect.name}ช่วยกำบัง", "engine"))
            elif category == "light" and skill == "stealth":
                out.append(DCFactor("lit_area", +2, f"{effect.name}ส่องสว่าง",
                                    "engine"))
        return out

    # --- the place itself ------------------------------------------------------------
    async def _environment_factors(self, *, location_id: str | None,
                                   skill: str | None) -> list[DCFactor]:
        """Only conditions the Location model actually records. `state` is a free JSON
        bag, so read it defensively and take only well-formed flags."""
        if not location_id or not skill:
            return []
        from app.models.location import Location

        loc = await self.session.get(Location, location_id)
        if loc is None:
            return []
        out: list[DCFactor] = []
        weather = (loc.weather or "").strip().lower()
        if weather and skill in _PERCEPTIVE:
            if any(w in weather for w in ("storm", "rain", "fog", "mist", "snow",
                                          "พายุ", "ฝน", "หมอก")):
                out.append(DCFactor("bad_weather", +2, "อากาศเลวร้าย", "engine"))
        state = loc.state if isinstance(loc.state, dict) else {}
        if bool(state.get("dark")) and skill in (_PERCEPTIVE | {"stealth"}):
            delta = -2 if skill == "stealth" else +3
            label = "ความมืดช่วยกำบัง" if skill == "stealth" else "มืด"
            out.append(DCFactor("location_dark", delta, label, "engine"))
        if bool(state.get("crowded")) and skill in ("stealth", "sleight_of_hand"):
            out.append(DCFactor("location_crowded", -2, "ผู้คนพลุกพล่าน", "engine"))
        return out
