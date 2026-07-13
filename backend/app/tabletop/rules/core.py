"""Primitive rules helpers for the authoritative ruleset — D&D 2024 (SRD 5.2.1).

See docs/rules-authority.md. This module is the small, edition-agnostic core:
- Six abilities and the standard modifier formula.
- Flat proficiency bonus by level.
- The skill -> ability map.
- The selectable-class / ancestry allowlists (validation).

Everything class-specific — features, resources, spellcasting, combat, rest — is
built ON these primitives in the reusable systems under `app/tabletop/` and the
typed definitions in `app/rules_content/registry.py`. What each CLASS can and can't
do mechanically is governed by its typed definition + `support_status`, not by this
module. Unsupported input is rejected loudly (RulesViolation), never silently faked.
"""
from __future__ import annotations

from app.core.errors import RulesViolation

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")

# The common 5e skills and the ability each keys off.
SKILL_TO_ABILITY: dict[str, str] = {
    "athletics": "str",
    "acrobatics": "dex",
    "sleight_of_hand": "dex",
    "stealth": "dex",
    "arcana": "int",
    "history": "int",
    "investigation": "int",
    "nature": "int",
    "religion": "int",
    "animal_handling": "wis",
    "insight": "wis",
    "medicine": "wis",
    "perception": "wis",
    "survival": "wis",
    "deception": "cha",
    "intimidation": "cha",
    "performance": "cha",
    "persuasion": "cha",
}
SUPPORTED_SKILLS = frozenset(SKILL_TO_ABILITY)

SUPPORTED_CLASSES = frozenset({"fighter", "rogue", "wizard", "cleric", "ranger", "bard"})
SUPPORTED_ANCESTRIES = frozenset(
    {"human", "elf", "dwarf", "halfling", "half-orc", "tiefling", "gnome"}
)


def ability_modifier(score: int) -> int:
    """Standard 5e modifier: floor((score - 10) / 2)."""
    return (score - 10) // 2


def proficiency_bonus_for_level(level: int) -> int:
    if level < 1:
        raise RulesViolation("level must be >= 1")
    return 2 + (level - 1) // 4


def ability_for_skill(skill: str) -> str:
    validate_skill(skill)
    return SKILL_TO_ABILITY[skill]


def validate_ability(ability: str) -> str:
    a = ability.lower()
    if a not in ABILITIES:
        raise RulesViolation(f"unsupported ability: {ability!r}")
    return a


def validate_skill(skill: str) -> str:
    s = skill.lower()
    if s not in SUPPORTED_SKILLS:
        raise RulesViolation(f"unsupported skill: {skill!r}")
    return s


def validate_class(char_class: str) -> str:
    c = char_class.lower()
    if c not in SUPPORTED_CLASSES:
        raise RulesViolation(
            f"unsupported class: {char_class!r} (supported: {sorted(SUPPORTED_CLASSES)})"
        )
    return c
