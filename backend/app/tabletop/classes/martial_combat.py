"""Deterministic martial combat rules — eligibility + scaling, engine-owned.

These are pure functions the combat resolver calls. Sneak Attack in particular must
fire only when its ACTUAL conditions hold (a finesse/ranged weapon, and either
advantage or an ally adjacent to the target, and not disadvantage) — never because
the narration "sounds sneaky". The narrator never decides any of this.
"""
from __future__ import annotations

from math import ceil


def sneak_attack_dice(rogue_level: int) -> int:
    """Number of d6 for Sneak Attack: 1 at L1, +1 every odd level (ceil(level/2))."""
    return max(1, ceil(rogue_level / 2))


def sneak_attack_eligible(*, weapon_finesse_or_ranged: bool, has_advantage: bool,
                          ally_adjacent_to_target: bool, has_disadvantage: bool) -> bool:
    """SRD 5.2.1 Sneak Attack conditions (all must hold):
    - the attack uses a finesse OR ranged weapon;
    - you have advantage on the attack, OR an ally of yours is within 5 ft of the
      target (and you don't have disadvantage);
    - you do NOT have disadvantage on the attack.
    """
    if not weapon_finesse_or_ranged:
        return False
    if has_disadvantage:
        return False
    return has_advantage or ally_adjacent_to_target


def extra_attacks(char_class: str, level: int, feature_keys: set[str]) -> int:
    """How many attacks one Attack action makes. Extra Attack (fighter/barbarian/
    monk etc.) grants a second at level 5+ when the character actually has it."""
    return 2 if (level >= 5 and "extra_attack" in feature_keys) else 1


# --- rage in combat -------------------------------------------------------------

_PHYSICAL = {"bludgeoning", "piercing", "slashing"}


def rage_damage_after_resistance(damage: int, damage_type: str, target_raging: bool) -> int:
    """A raging target resists physical damage (halved, round down)."""
    if target_raging and (damage_type or "").lower() in _PHYSICAL:
        return damage // 2
    return damage


def rage_attack_bonus(attacker_rage: dict | None, damage_type: str, is_melee_strength: bool) -> int:
    """Rage adds its damage bonus to a melee STRENGTH weapon attack's damage."""
    if not attacker_rage or not is_melee_strength:
        return 0
    return int(attacker_rage.get("damage_bonus", 0))
