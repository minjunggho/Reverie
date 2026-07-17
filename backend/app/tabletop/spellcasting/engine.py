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
    # Effects this cast actually created and committed. The narrator reads these
    # rather than deciding for itself whether the spell did anything.
    effects: list = field(default_factory=list)       # list[GrantedEffect]
    # An observer's reaction to a world effect, when one was created.
    observations: list = field(default_factory=list)  # list[dict]
    # A rules limit the engine applied to the requested cast (e.g. Minor Illusion
    # asked for image AND sound → one had to be chosen). Surfaced so the player is
    # told WHY, never silently given something other than what they asked for.
    adjustments: list[str] = field(default_factory=list)


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
        effect_targets: list[str] | None = None,
        effect_params: dict | None = None,
        location_id: str | None = None,
    ) -> SpellCastOutcome:
        """Cast `spell_key` at `slot_level` (defaults to the spell's own level).
        `target_acs`/`target_save_mods` map a target entity_ref to its AC / save
        modifier; targets without the needed number are skipped for that step.
        `ritual=True` casts a ritual-tagged spell without spending a slot.

        `effect_targets` names who the spell's declared effects land on. It is
        SEPARATE from target_acs/target_save_mods because those only exist for
        attack/save spells — a buff like Guidance has neither, and deriving its
        target from combat numbers is exactly how the target used to be dropped.
        `effect_params` carries validated cast-specific detail (an illusion's
        description and mode)."""
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
        caster_ref = entity_ref("character", character.id)
        # WHO the declared effects land on. A "self" scope ignores stated targets; an
        # unstated target for a self-castable buff means the caster. Falling back to
        # the combat `targets` list keeps attack/save spells that also declare an
        # effect coherent.
        effect_subjects = list(effect_targets or [])
        if not effect_subjects and spell.effects:
            if any(e.target_scope == "self" for e in spell.effects):
                effect_subjects = [caster_ref]
            elif targets:
                effect_subjects = list(targets)
            else:
                effect_subjects = [caster_ref]
        outcome = SpellCastOutcome(
            spell_key=spell.name, caster_ref=caster_ref,
            slot_level=0 if spell.is_cantrip else level,
            targets=targets or effect_subjects,
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
        #    Ending a prior concentration must also end the effects it was holding
        #    up, which is why this runs BEFORE the new effects are created.
        camp_id = campaign_id or character.campaign_id
        if spell.concentration:
            from app.tabletop.effects import ConcentrationService

            prior = await ConcentrationService(self.session, self.dice).current(character.id)
            if prior is not None:
                await self._end_effects_of_concentration(prior, camp_id)
            await ConcentrationService(self.session, self.dice).begin(
                character=character, name=spell.name_th_hint, spell_key=spell.name,
                targets=effect_subjects or targets, session_id=session_id,
                scene_id=scene_id)
            outcome.concentration = True

        # 5) effects — the spell's DECLARED effects become real, persisted state.
        #    This is what makes a utility spell do something: without it the cast
        #    resolves to nothing and can only echo its own name back.
        outcome.effects = await self._create_effects(
            spell=spell, character=character, campaign_id=camp_id,
            subjects=effect_subjects, params=effect_params or {},
            scene_id=scene_id, location_id=location_id,
            adjustments=outcome.adjustments)

        outcome.line_th = _cast_line(spell, outcome)

        # 6) record — one canonical SPELL_CAST event with the mechanical summary.
        from app.services.events import EventService

        await EventService(self.session).record(
            campaign_id=camp_id, session_id=session_id,
            scene_id=scene_id, event_type=EventType.SPELL_CAST,
            actor_entity=outcome.caster_ref,
            target_entities=targets or effect_subjects,
            visibility=Visibility.PARTY,
            mechanical_changes={"damage": outcome.damage, "healing": outcome.healing,
                                "slot_level": outcome.slot_level},
            payload={"spell": spell.name, "summary": outcome.line_th,
                     "concentration": outcome.concentration,
                     # The effects are part of the record of what happened, so a
                     # later turn (and the narrator's memory) can see that this cast
                     # left something behind.
                     "effects": [{"id": e.effect_id, "kind": e.kind,
                                  "subject": e.subject_ref} for e in outcome.effects],
                     "adjustments": outcome.adjustments},
            narrative_significance=18,
        )
        return outcome


    async def _create_effects(
        self, *, spell: SpellDef, character: Character, campaign_id: str,
        subjects: list[str], params: dict, scene_id: str | None,
        location_id: str | None, adjustments: list[str],
    ) -> list:
        """Turn each declared SpellEffectDef into committed state."""
        if not spell.effects:
            return []
        from app.tabletop.effects import EffectService

        service = EffectService(self.session)
        created = []
        for effect_def in spell.effects:
            effect_params = dict(params)
            if effect_def.kind == "world_effect":
                effect_params = self._validated_world_params(
                    spell, effect_def, params, adjustments)
            # A point-scoped effect (an illusion, a fog bank) exists at a PLACE, not
            # on a creature — it gets no subject, and is found by scene/location.
            scopes = [None] if effect_def.target_scope == "point" else (subjects or [None])
            for subject in scopes:
                created.append(await service.grant_spell_effect(
                    spell=spell, effect_def=effect_def, caster=character,
                    subject_ref=subject, campaign_id=campaign_id,
                    scene_id=scene_id, location_id=location_id,
                    params=effect_params))
        return created

    @staticmethod
    def _validated_world_params(
        spell: SpellDef, effect_def, params: dict, adjustments: list[str],
    ) -> dict:
        """Enforce the effect's declared limits on what the player asked for.

        The SRD limit that matters at the table: Minor Illusion creates an image OR a
        sound, not both. When a player asks for both (a cat that dances AND sings),
        the engine keeps the first declared mode and records WHY — the cast is not
        silently reinterpreted, and it is not rejected outright either.
        """
        out = dict(params)
        requested = [m for m in (params.get("modes") or []) if m in effect_def.modes]
        if not requested:
            requested = effect_def.modes[:1]
        if effect_def.choose_one_mode and len(requested) > 1:
            kept, dropped = requested[0], requested[1:]
            adjustments.append(
                f"{spell.name_th_hint} สร้างได้อย่างใดอย่างหนึ่งเท่านั้น — "
                f"เลือก{_MODE_TH.get(kept, kept)}ไว้ "
                f"(ตัด{'/'.join(_MODE_TH.get(d, d) for d in dropped)}ออก)")
            requested = [kept]
        out["modes"] = requested
        return out

    async def _end_effects_of_concentration(self, prior, campaign_id: str) -> None:
        """When a concentration spell is replaced, the effects it sustained end too.
        Otherwise a replaced Guidance would keep handing out dice forever."""
        if not prior.spell_key:
            return
        from sqlalchemy import select as _select

        from app.models.progression import ActiveEffect as _AE
        from app.tabletop.effects import EffectService

        rows = list((await self.session.execute(_select(_AE).where(
            _AE.campaign_id == campaign_id,
            _AE.character_id == prior.character_id,
            _AE.spell_key == prior.spell_key,
            _AE.kind != "",
            _AE.active.is_(True),
        ))).scalars())
        service = EffectService(self.session)
        for row in rows:
            await service.dismiss(row, reason="concentration_replaced")


_MODE_TH = {"image": "ภาพ", "sound": "เสียง"}


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
    """The engine-owned mechanical summary of what this cast DID.

    This function is why 'ภาพลวงย่อม' was the entire response to Minor Illusion: with
    no attack, save, damage or healing to report, `bits` held only the spell's name.
    A cast now also reports the effects it created, so a utility spell describes real
    committed state instead of echoing its own name.
    """
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
    for effect in o.effects:
        line = _effect_line(effect)
        if line:
            bits.append(line)
    if o.concentration:
        bits.append("· ต้องเพ่งสมาธิ")
    return "  |  ".join(bits)


def _effect_line(effect) -> str:
    """One human line for a created effect, from the effect's own declaration."""
    data = effect.data or {}
    if effect.kind == "roll_bonus":
        applies = data.get("applies_to") or []
        scope = "  ".join(_ROLL_TH.get(a, a) for a in applies)
        tail = " (ใช้ครั้งเดียว)" if data.get("consumed_on_use") else ""
        return f"{data.get('dice')} เพิ่มใน{scope}{tail}"
    if effect.kind == "world_effect":
        modes = data.get("modes") or []
        mode_th = "/".join(_MODE_TH.get(m, m) for m in modes)
        described = (data.get("description") or "").strip()
        head = f"สร้าง{mode_th}ลวง" if data.get("category") == "illusion" else f"สร้าง{mode_th}"
        return f"{head}: {described}" if described else head
    if effect.kind == "ac_bonus":
        return f"AC +{data.get('bonus')}"
    if effect.kind == "condition":
        return f"สภาวะ: {data.get('condition')}"
    return effect.note_th or ""


_ROLL_TH = {
    "ability_check": "การตรวจความสามารถ",
    "saving_throw": "การเซฟ",
    "attack_roll": "การโจมตี",
}
