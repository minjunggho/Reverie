"""Derivation engine — computes every derived value with an explainable breakdown.

Derived values are never the only stored truth (mandate §10). The engine can
answer "ทำไม Arcana +5?" with the actual composition: INT +3, Proficiency +2.
Pure functions over Character + RulesRegistry; no I/O, no LLM, no dice.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.character import Character
from app.rules_content import get_registry
from app.tabletop.rules.core import (
    SKILL_TO_ABILITY,
    ability_modifier,
    proficiency_bonus_for_level,
    validate_ability,
    validate_skill,
)


@dataclass
class Breakdown:
    total: int
    parts: list[tuple[str, int]] = field(default_factory=list)

    def line_th(self) -> str:
        bits = " ".join(f"{label} {v:+d}" for label, v in self.parts)
        return f"{bits} = {self.total:+d}"


def save_bonus(char: Character, ability: str) -> Breakdown:
    a = validate_ability(ability)
    parts = [(a.upper(), ability_modifier(char.ability_score(a)))]
    if a in (char.save_proficiencies or []):
        parts.append(("Proficiency", proficiency_bonus_for_level(char.level)))
    return Breakdown(total=sum(v for _, v in parts), parts=parts)


def skill_bonus(char: Character, skill: str) -> Breakdown:
    from app.tabletop.classes.bard import jack_of_all_trades_bonus

    s = validate_skill(skill)
    a = SKILL_TO_ABILITY[s]
    parts = [(a.upper(), ability_modifier(char.ability_score(a)))]
    pb = proficiency_bonus_for_level(char.level)
    if s in (char.expertise or []):
        parts.append(("Expertise", pb * 2))
    elif s in (char.proficiencies or []):
        parts.append(("Proficiency", pb))
    else:
        # Jack of All Trades: a bard (L2+) adds half proficiency to checks it is
        # NOT proficient in. 0 (and so no part) for everyone else.
        joat = jack_of_all_trades_bonus(char.char_class, char.level, pb)
        if joat:
            parts.append(("Jack of All Trades", joat))
    return Breakdown(total=sum(v for _, v in parts), parts=parts)


def passive_perception(char: Character) -> int:
    return 10 + skill_bonus(char, "perception").total


def initiative_bonus(char: Character) -> int:
    return ability_modifier(char.dex_score)


def spellcasting_block(char: Character) -> dict | None:
    """Save DC / spell attack / ability for the character's class, if a caster."""
    cls = get_registry().get_class(char.char_class)
    sc = cls.spellcasting
    if sc is None:
        return None
    mod = ability_modifier(char.ability_score(sc.ability))
    pb = proficiency_bonus_for_level(char.level)
    return {
        "ability": sc.ability,
        "save_dc": 8 + pb + mod,
        "attack_bonus": pb + mod,
        "prepared_count": sc.prepared_count,
        "cantrips_known": sc.cantrips_known,
    }


def max_hp_level_1(char_class: str, con_score: int, species: str) -> int:
    reg = get_registry()
    cls = reg.get_class(char_class)
    hp = cls.hit_die + ability_modifier(con_score)
    for trait in reg.get_species(species).traits:
        hp += trait.hp_per_level  # e.g. Dwarven Toughness
    return max(1, hp)


def armor_class(char_class: str, dex_score: int, *, con_score: int = 10,
                wis_score: int = 10) -> int:
    """From the class's starting-equipment armor formula (SRD armor rules).
    Barbarian/Monk Unarmored Defense add a second ability (CON / WIS)."""
    base = get_registry().get_class(char_class).base_ac
    dex = ability_modifier(dex_score)
    if base.kind == "unarmored":
        ac = 10 + dex
    elif base.kind == "unarmored_con":              # Barbarian Unarmored Defense
        ac = 10 + dex + ability_modifier(con_score)
    elif base.kind == "unarmored_wis":              # Monk Unarmored Defense
        ac = 10 + dex + ability_modifier(wis_score)
    elif base.kind == "flat":
        ac = base.value
    elif base.kind == "light":
        ac = base.value + dex
    else:  # medium
        ac = base.value + min(dex, base.dex_cap if base.dex_cap is not None else 2)
    if base.shield:
        ac += 2
    return ac


def resistances(grants: list) -> set[str]:
    """Damage types this character resists, from trait grants (e.g. dwarf poison)."""
    out: set[str] = set()
    for g in grants:
        out.update((g.data or {}).get("resistances", []))
    return out
