"""EffectService — the one place a declared spell effect becomes persisted state,
gets read by a rule, and eventually ends.

This closes the gap that made Guidance and Minor Illusion inert. A spell declares
what it does (registry.SpellEffectDef); SpellEngine asks this service to create it;
the roll path asks this service what dice an actor is owed; the NPC observer model
asks it what exists in the scene. No spell is named anywhere in this file — the
behaviour comes from the declaration.

Lifecycle, in engine terms:
  grant    — a cast creates ActiveEffect rows from the spell's declared effects.
  read     — bonus_grants_for() / ac_bonus_for() / world_effects_in() answer rules.
  consume  — a roll_bonus with consumed_on_use ends the moment it feeds a roll.
  expire   — the game clock ends anything past its duration.

Expiry is evaluated against Campaign.current_game_time (in-world minutes), so an
effect that outlives its duration cannot survive a reload: the read paths filter it
out even before the sweep marks it inactive.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import entity_ref
from app.core.logging import get_logger
from app.models.campaign import Campaign
from app.models.character import Character
from app.models.enums import EventType, Visibility
from app.models.progression import ActiveEffect
from app.rules_content.registry import SpellDef, SpellEffectDef
from app.tabletop.dice import BonusGrant

log = get_logger(__name__)

# The roll kinds a roll_bonus may declare, matching SpellEffectDef.applies_to.
ROLL_ABILITY_CHECK = "ability_check"
ROLL_SAVING_THROW = "saving_throw"
ROLL_ATTACK = "attack_roll"


@dataclass
class GrantedEffect:
    """What a cast actually created — returned to the caster so narration can
    describe a real, committed effect instead of guessing."""
    effect_id: str
    kind: str
    name: str
    subject_ref: str | None
    note_th: str = ""
    data: dict | None = None


class EffectService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- clock ---------------------------------------------------------------
    async def game_time(self, campaign_id: str) -> int:
        campaign = await self.session.get(Campaign, campaign_id)
        return int(campaign.current_game_time) if campaign else 0

    @staticmethod
    def _expired(effect: ActiveEffect, now: int) -> bool:
        """An effect with no duration never expires on the clock (it ends by
        concentration, consumption or dismissal)."""
        if effect.duration_minutes is None:
            return False
        return now >= effect.started_game_time + effect.duration_minutes

    # --- grant ---------------------------------------------------------------
    async def grant(
        self, *, campaign_id: str, owner: Character, name: str, kind: str,
        subject_ref: str | None, spell_key: str | None = None,
        requires_concentration: bool = False, duration_minutes: int | None = None,
        scene_id: str | None = None, location_id: str | None = None,
        data: dict | None = None, game_time: int | None = None,
    ) -> ActiveEffect:
        now = game_time if game_time is not None else await self.game_time(campaign_id)
        effect = ActiveEffect(
            campaign_id=campaign_id, character_id=owner.id, name=name, kind=kind,
            spell_key=spell_key, subject_ref=subject_ref,
            requires_concentration=requires_concentration,
            targets=[subject_ref] if subject_ref else [],
            started_game_time=now, duration_minutes=duration_minutes,
            scene_id=scene_id, location_id=location_id, data=data or {},
        )
        self.session.add(effect)
        await self.session.flush()
        log.info(
            "effect granted", extra={"effect_id": effect.id, "kind": kind,
                                     "spell": spell_key, "campaign_id": campaign_id,
                                     "owner": owner.id, "subject": subject_ref,
                                     "duration_minutes": duration_minutes,
                                     "started_game_time": now})
        return effect

    async def grant_spell_effect(
        self, *, spell: SpellDef, effect_def: SpellEffectDef, caster: Character,
        subject_ref: str | None, campaign_id: str, scene_id: str | None = None,
        location_id: str | None = None, params: dict | None = None,
        game_time: int | None = None,
    ) -> GrantedEffect:
        """Create ONE declared effect of a spell. `params` carries the validated,
        cast-specific detail (an illusion's description and chosen mode), which the
        definition cannot know."""
        data = {"note_th": effect_def.note_th, **(params or {})}
        if effect_def.kind == "roll_bonus":
            data.update({"dice": effect_def.dice,
                         "applies_to": list(effect_def.applies_to),
                         "abilities": [a.lower() for a in effect_def.abilities],
                         "consumed_on_use": effect_def.consumed_on_use})
        elif effect_def.kind == "ac_bonus":
            data["bonus"] = effect_def.bonus
        elif effect_def.kind == "condition":
            data["condition"] = effect_def.condition
        elif effect_def.kind == "world_effect":
            data.update({"category": effect_def.category,
                         "detect_ability": effect_def.detect_ability,
                         "detect_skill": effect_def.detect_skill,
                         "insubstantial": effect_def.insubstantial,
                         # Nobody has seen through it yet.
                         "discovered_by": [], "investigated": False})

        effect = await self.grant(
            campaign_id=campaign_id, owner=caster, name=spell.name_th_hint,
            kind=effect_def.kind, subject_ref=subject_ref, spell_key=spell.name,
            # Concentration ownership stays with ConcentrationService; an effect row
            # created here never double-books the caster's single concentration slot.
            requires_concentration=False,
            duration_minutes=spell.duration_minutes,
            scene_id=scene_id, location_id=location_id, data=data,
            game_time=game_time,
        )
        return GrantedEffect(
            effect_id=effect.id, kind=effect.kind, name=effect.name,
            subject_ref=subject_ref, note_th=effect_def.note_th, data=effect.data,
        )

    # --- read: rules integration ---------------------------------------------
    async def active_for_subject(
        self, *, campaign_id: str, subject_ref: str, kind: str | None = None,
        game_time: int | None = None,
    ) -> list[ActiveEffect]:
        """Live effects acting on `subject_ref`, clock-expired rows excluded."""
        now = game_time if game_time is not None else await self.game_time(campaign_id)
        stmt = select(ActiveEffect).where(
            ActiveEffect.campaign_id == campaign_id,
            ActiveEffect.subject_ref == subject_ref,
            ActiveEffect.active.is_(True),
        )
        if kind is not None:
            stmt = stmt.where(ActiveEffect.kind == kind)
        rows = list((await self.session.execute(stmt)).scalars())
        return [r for r in rows if not self._expired(r, now)]

    async def bonus_grants_for(
        self, *, campaign_id: str, subject_ref: str, roll_type: str,
        ability: str | None = None, game_time: int | None = None,
    ) -> list[BonusGrant]:
        """The extra dice `subject_ref` is owed on a roll of `roll_type`.

        Eligibility is the effect's own declaration — which roll kinds it applies to,
        and which abilities (empty = any). This is what makes Guidance help a
        Deception check but not an attack roll, without Guidance being named here.
        """
        effects = await self.active_for_subject(
            campaign_id=campaign_id, subject_ref=subject_ref, kind="roll_bonus",
            game_time=game_time)
        grants: list[BonusGrant] = []
        for effect in effects:
            data = effect.data or {}
            if roll_type not in (data.get("applies_to") or []):
                continue
            abilities = [a.lower() for a in (data.get("abilities") or [])]
            if abilities and (ability or "").lower() not in abilities:
                continue
            expression = data.get("dice")
            if not expression:
                continue
            grants.append(BonusGrant(
                source=effect.id, label=effect.name, expression=expression,
                consumed_on_use=bool(data.get("consumed_on_use")),
            ))
        return grants

    async def ac_bonus_for(self, *, campaign_id: str, subject_ref: str,
                           game_time: int | None = None) -> int:
        effects = await self.active_for_subject(
            campaign_id=campaign_id, subject_ref=subject_ref, kind="ac_bonus",
            game_time=game_time)
        return sum(int((e.data or {}).get("bonus") or 0) for e in effects)

    async def world_effects_in(
        self, *, campaign_id: str, scene_id: str | None = None,
        location_id: str | None = None, game_time: int | None = None,
    ) -> list[ActiveEffect]:
        """Live world effects (illusions, light, fog) present in a scene/location —
        how a later turn finds the illusion cast two turns ago."""
        now = game_time if game_time is not None else await self.game_time(campaign_id)
        stmt = select(ActiveEffect).where(
            ActiveEffect.campaign_id == campaign_id,
            ActiveEffect.kind == "world_effect",
            ActiveEffect.active.is_(True),
        )
        if scene_id:
            stmt = stmt.where(ActiveEffect.scene_id == scene_id)
        elif location_id:
            stmt = stmt.where(ActiveEffect.location_id == location_id)
        rows = list((await self.session.execute(stmt)).scalars())
        return [r for r in rows if not self._expired(r, now)]

    # --- end -----------------------------------------------------------------
    async def consume(self, effect_ids: list[str], *, reason: str = "consumed") -> None:
        """End effects that were spent by a roll. Called only AFTER the roll they fed
        is committed, so a consumed effect always bought something."""
        if not effect_ids:
            return
        rows = list((await self.session.execute(select(ActiveEffect).where(
            ActiveEffect.id.in_(effect_ids)))).scalars())
        for effect in rows:
            effect.active = False
            log.info("effect consumed",
                     extra={"effect_id": effect.id, "kind": effect.kind,
                            "spell": effect.spell_key, "reason": reason})

    async def dismiss(self, effect: ActiveEffect, *, reason: str) -> None:
        effect.active = False
        log.info("effect dismissed", extra={"effect_id": effect.id,
                                            "kind": effect.kind, "reason": reason})

    async def expire_due(self, *, campaign_id: str, game_time: int | None = None,
                         session_id: str | None = None,
                         scene_id: str | None = None) -> list[ActiveEffect]:
        """Sweep effects whose duration has run out, recording one event each so the
        table learns the illusion flickered out rather than silently losing it."""
        now = game_time if game_time is not None else await self.game_time(campaign_id)
        rows = list((await self.session.execute(select(ActiveEffect).where(
            ActiveEffect.campaign_id == campaign_id,
            ActiveEffect.active.is_(True),
        ))).scalars())
        ended: list[ActiveEffect] = []
        for effect in rows:
            if not self._expired(effect, now):
                continue
            effect.active = False
            ended.append(effect)
            log.info("effect expired",
                     extra={"effect_id": effect.id, "kind": effect.kind,
                            "spell": effect.spell_key, "game_time": now,
                            "started_game_time": effect.started_game_time,
                            "duration_minutes": effect.duration_minutes})
        if ended:
            from app.services.events import EventService

            events = EventService(self.session)
            for effect in ended:
                await events.record(
                    campaign_id=campaign_id, session_id=session_id,
                    scene_id=effect.scene_id or scene_id,
                    event_type=EventType.NPC_STATE_CHANGED,
                    actor_entity=entity_ref("character", effect.character_id),
                    visibility=Visibility.PARTY,
                    payload={"summary": f"{effect.name} หมดฤทธิ์แล้ว",
                             "kind": "effect_expired", "spell": effect.spell_key},
                    narrative_significance=10,
                )
        return ended
