"""SpellEngine — the one authoritative way any class casts a spell (SRD 5.2.1).

Every caster (wizard/SPELLBOOK, cleric/PREPARED, bard/KNOWN, and — when unlocked —
warlock/PACT_MAGIC) resolves through here, using the SAME registry that character
creation selects from. A spell is only castable if the engine can resolve it
honestly, which is exactly what registry validation enforces at load.

cast() is atomic within the caller's transaction:
  1. authorize — the caster actually knows/has-prepared the spell.
  2. pay — cantrips are free; a leveled spell spends a slot of the chosen level via
     the ResourceEngine (rejecting insufficient slots).
  3. resolve — deterministic dice: a spell attack roll vs target AC, or a target
     save vs the caster's DC, then damage/healing; the LLM never rolls.
  4. concentrate — a concentration spell begins a ConcentrationService effect
     (ending any prior one).
  5. record — a canonical SPELL_CAST event (+ the mechanical sub-events).

No mechanic is invented: attack/save/damage/healing/concentration all come from the
typed SpellDef. Anything the definition doesn't specify simply doesn't happen.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RulesViolation, ValidationError
from app.core.ids import entity_ref
from app.models.character import Character
from app.models.enums import EventType, Visibility
from app.models.progression import CharacterSpell
from app.rules_content import get_registry
from app.rules_content.registry import SpellDef
from app.tabletop.dice import DiceEngine
from app.tabletop.rules.core import ability_modifier, proficiency_bonus_for_level

_DICE_RE = re.compile(r"(\d+)d(\d+)\s*(\w+)?")


@dataclass
class SpellcastingProfile:
    """A caster's full spell picture, derived from the class definition + the
    character's own CharacterSpell rows. Non-casters get is_caster=False."""
    is_caster: bool
    model: str = "NONE"
    ability: str = ""
    save_dc: int = 0
    attack_bonus: int = 0
    cantrips: list[str] = field(default_factory=list)     # spell keys
    known: list[str] = field(default_factory=list)        # book/known spell keys
    prepared: list[str] = field(default_factory=list)     # castable leveled spells
    slot_resources: dict[int, str] = field(default_factory=dict)  # spell level -> resource id

    def can_cast_key(self, spell_key: str) -> bool:
        return spell_key in self.cantrips or spell_key in self.prepared


async def spellcasting_profile(session: AsyncSession, character: Character) -> SpellcastingProfile:
    reg = get_registry()
    cls = reg.get_class(character.char_class)
    sc = cls.spellcasting
    if sc is None:
        return SpellcastingProfile(is_caster=False)

    mod = ability_modifier(character.ability_score(sc.ability))
    pb = proficiency_bonus_for_level(character.level)
    rows = list((await session.execute(
        select(CharacterSpell).where(CharacterSpell.character_id == character.id)
    )).scalars())
    cantrips = [r.spell_key for r in rows if r.kind == "cantrip"]
    # "Prepared/castable" = known/known-prepared spells the character can cast now.
    # SPELLBOOK/PREPARED: rows flagged prepared. KNOWN: all known are castable.
    if sc.model in ("KNOWN_SPELLS", "PACT_MAGIC"):
        prepared = [r.spell_key for r in rows if r.kind in ("known", "book")]
    else:
        prepared = [r.spell_key for r in rows if r.kind in ("known", "book") and r.prepared]
    known = [r.spell_key for r in rows if r.kind in ("known", "book")]
    slots = {int(lvl): rid for lvl, rid in sc.slot_resources.items()}
    return SpellcastingProfile(
        is_caster=True, model=sc.model, ability=sc.ability,
        save_dc=8 + pb + mod, attack_bonus=pb + mod,
        cantrips=cantrips, known=known, prepared=prepared, slot_resources=slots,
    )


@dataclass
class SpellCastOutcome:
    spell_key: str
    caster_ref: str
    slot_level: int                    # 0 for a cantrip
    targets: list[str] = field(default_factory=list)
    attack: dict | None = None         # {natural_roll, total, target_ac, hit}
    saves: list[dict] = field(default_factory=list)   # per target {ability, dc, total, passed}
    damage: int = 0
    damage_type: str = ""
    healing: int = 0
    concentration: bool = False
    line_th: str = ""                  # engine-owned mechanical summary


class SpellEngine:
    def __init__(self, session: AsyncSession, dice: DiceEngine) -> None:
        self.session = session
        self.dice = dice
        self.reg = get_registry()

    async def cast(
        self, *, character: Character, spell_key: str, slot_level: int | None = None,
        target_acs: dict[str, int] | None = None, target_save_mods: dict[str, int] | None = None,
        ritual: bool = False,
        session_id: str | None = None, scene_id: str | None = None,
        campaign_id: str | None = None,
    ) -> SpellCastOutcome:
        """Cast `spell_key` at `slot_level` (defaults to the spell's own level).
        `target_acs`/`target_save_mods` map a target entity_ref to its AC / save
        modifier; targets without the needed number are skipped for that step.
        `ritual=True` casts a ritual-tagged spell without spending a slot."""
        spell = self.reg.get_spell(spell_key)
        profile = await spellcasting_profile(self.session, character)
        if not profile.is_caster:
            raise RulesViolation(f"{character.name} ไม่ใช่ผู้ใช้เวท")
        if not profile.can_cast_key(spell.name):
            raise RulesViolation(
                f"{character.name} ยังไม่ได้เตรียม/รู้คาถา {spell.name_th_hint}")

        level = slot_level if slot_level is not None else spell.level
        # 2) pay: cantrips free; a RITUAL cast is slot-free (10 minutes longer, no
        #    slot spent) when the spell has the ritual tag and the caster is not
        #    forcing a specific slot; a leveled spell otherwise spends a slot.
        ritual_cast = ritual and spell.ritual and slot_level is None
        if not spell.is_cantrip and not ritual_cast:
            if level < spell.level:
                raise ValidationError(
                    f"ต้องใช้ช่องเวทระดับ {spell.level} ขึ้นไป (ขอ {level})")
            resource_id = profile.slot_resources.get(level)
            if resource_id is None:
                raise ValidationError(f"ไม่มีช่องเวทระดับ {level}")
            from app.tabletop.resources import ResourceEngine

            await ResourceEngine(self.session).spend(character.id, resource_id, 1)

        target_acs = target_acs or {}
        target_save_mods = target_save_mods or {}
        targets = sorted(set(target_acs) | set(target_save_mods))
        outcome = SpellCastOutcome(
            spell_key=spell.name, caster_ref=entity_ref("character", character.id),
            slot_level=0 if spell.is_cantrip else level, targets=targets,
        )

        # 3) resolve — attack roll, saves, damage, healing (only what the def says).
        dice, dtype = _parse_dice(spell.damage)
        base_damage = 0
        if spell.attack != "none" and targets:
            tref = targets[0]
            roll = self.dice.resolve_attack(
                attack_modifier=profile.attack_bonus, target_ac=target_acs.get(tref, 10))
            outcome.attack = {"natural_roll": roll.natural_roll, "total": roll.total,
                              "target_ac": target_acs.get(tref, 10),
                              "hit": roll.outcome == "success"}
            if roll.outcome == "success" and dice:
                dmg, _ = self.dice.resolve_damage(dice=dice)
                base_damage = dmg
                outcome.damage, outcome.damage_type = dmg, dtype
        elif spell.save_ability and targets:
            rolled_damage = None
            if dice:
                rolled_damage, _ = self.dice.resolve_damage(dice=dice)
            for tref in targets:
                save = self.dice.resolve_saving_throw(
                    modifier=target_save_mods.get(tref, 0), dc=profile.save_dc,
                    ability=spell.save_ability)
                passed = save.outcome == "success"
                outcome.saves.append({"target": tref, "ability": spell.save_ability,
                                      "dc": profile.save_dc, "total": save.total,
                                      "passed": passed})
                if rolled_damage is not None:
                    got = rolled_damage
                    if passed:
                        got = rolled_damage // 2 if spell.half_on_save else 0
                    outcome.damage += got
                    outcome.damage_type = dtype
        elif dice and not spell.healing:
            # No attack, no save (e.g. Magic Missile — automatic force damage).
            dmg, _ = self.dice.resolve_damage(dice=dice)
            outcome.damage, outcome.damage_type = dmg, dtype

        heal_dice, _ = _parse_dice(spell.healing)
        if heal_dice:
            heal_mod = ability_modifier(character.ability_score(profile.ability))
            healed, _ = self.dice.resolve_damage(dice=heal_dice, flat_modifier=heal_mod)
            outcome.healing = max(0, healed)

        # 4) concentration — begins/replaces the caster's single concentration effect.
        if spell.concentration:
            from app.tabletop.effects import ConcentrationService

            await ConcentrationService(self.session, self.dice).begin(
                character=character, name=spell.name_th_hint, spell_key=spell.name,
                targets=targets, session_id=session_id, scene_id=scene_id)
            outcome.concentration = True

        outcome.line_th = _cast_line(spell, outcome)

        # 5) record — one canonical SPELL_CAST event with the mechanical summary.
        from app.services.events import EventService

        await EventService(self.session).record(
            campaign_id=campaign_id or character.campaign_id, session_id=session_id,
            scene_id=scene_id, event_type=EventType.SPELL_CAST,
            actor_entity=outcome.caster_ref, target_entities=targets,
            visibility=Visibility.PARTY,
            mechanical_changes={"damage": outcome.damage, "healing": outcome.healing,
                                "slot_level": outcome.slot_level},
            payload={"spell": spell.name, "summary": outcome.line_th,
                     "concentration": outcome.concentration},
            narrative_significance=18,
        )
        return outcome


def _parse_dice(expr: str | None) -> tuple[list[int], str]:
    """'1d10 fire' -> ([10], 'fire'); '2d6' -> ([6,6], ''); None -> ([], '')."""
    if not expr:
        return [], ""
    m = _DICE_RE.search(expr)
    if not m:
        return [], ""
    count, sides, dtype = int(m.group(1)), int(m.group(2)), (m.group(3) or "")
    return [sides] * count, dtype


def _cast_line(spell: SpellDef, o: SpellCastOutcome) -> str:
    bits = [spell.name_th_hint]
    if o.attack is not None:
        verb = "โดน" if o.attack["hit"] else "พลาด"
        bits.append(f"โจมตีเวท {o.attack['total']} vs AC {o.attack['target_ac']} — {verb}")
    for s in o.saves:
        bits.append(f"เซฟ {s['ability'].upper()} {s['total']} vs DC {s['dc']} — "
                    f"{'ผ่าน' if s['passed'] else 'ไม่ผ่าน'}")
    if o.damage:
        bits.append(f"ดาเมจ {o.damage}{(' ' + o.damage_type) if o.damage_type else ''}")
    if o.healing:
        bits.append(f"ฟื้น {o.healing} HP")
    if o.concentration:
        bits.append("· ต้องเพ่งสมาธิ")
    return "  |  ".join(bits)
