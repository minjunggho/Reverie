"""Authoritative server-side dice engine.

Every die is produced HERE, via an injected `Randomness`. The LLM has no reference
to this engine and no code path that produces a die, a modifier, or an outcome —
that is proven in the dice tests. Results are plain structured values the engine
turns into events.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.randomness import Randomness

_BONUS_RE = re.compile(r"^\s*(\d+)\s*d\s*(\d+)\s*$", re.I)


def _parse_bonus_expression(expr: str) -> list[int]:
    """'1d4' -> [4]; '2d6' -> [6, 6]; anything else -> [] (contributes nothing)."""
    m = _BONUS_RE.match(expr or "")
    if not m:
        return []
    count, sides = int(m.group(1)), int(m.group(2))
    if count < 1 or sides < 2:
        return []
    return [sides] * count


@dataclass
class BonusGrant:
    """A pending offer of extra dice, produced by the effect layer and handed to the
    dice engine. Declaring the grant and rolling it are separate steps so the roll
    can be PREVIEWED to the player ("คำชี้นำ: +1d4") before it is made."""
    source: str                # ActiveEffect id
    label: str                 # human label
    expression: str            # "1d4"
    consumed_on_use: bool = False


@dataclass
class BonusDie:
    """One extra die contributed to a roll by an effect (Guidance's 1d4, Bless's
    1d4). `source` is the effect id so the roll can report — and then consume —
    exactly the effects that fed it."""
    source: str                # ActiveEffect id
    label: str                 # human label, e.g. "คำชี้นำ"
    expression: str            # "1d4"
    rolls: list[int] = field(default_factory=list)
    total: int = 0


@dataclass
class RollResult:
    natural_roll: int          # the kept d20 face
    all_rolls: list[int]       # both faces when advantage/disadvantage applied
    modifier: int
    total: int
    dc: int | None = None
    outcome: str | None = None  # "success" | "failure" | None
    advantage: bool = False
    disadvantage: bool = False
    # Extra dice granted by active effects. Part of `total` already; carried here so
    # the roll can be shown honestly ("+ คำชี้นำ 1d4 → 3") and so the pipeline knows
    # which effects to consume.
    bonus_dice: list[BonusDie] = field(default_factory=list)

    @property
    def bonus_total(self) -> int:
        return sum(b.total for b in self.bonus_dice)

    def as_dict(self) -> dict:
        return {
            "natural_roll": self.natural_roll,
            "all_rolls": self.all_rolls,
            "modifier": self.modifier,
            "total": self.total,
            "dc": self.dc,
            "outcome": self.outcome,
            "advantage": self.advantage,
            "disadvantage": self.disadvantage,
            "bonus_dice": [
                {"source": b.source, "label": b.label, "expression": b.expression,
                 "rolls": b.rolls, "total": b.total}
                for b in self.bonus_dice
            ],
        }


@dataclass
class AbilityCheckResult(RollResult):
    ability: str = ""
    skill: str | None = None
    proficient: bool = False


@dataclass
class AttackResult:
    attack: RollResult
    hit: bool
    target_ac: int
    damage: int = 0
    damage_rolls: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "attack": self.attack.as_dict(),
            "hit": self.hit,
            "target_ac": self.target_ac,
            "damage": self.damage,
            "damage_rolls": self.damage_rolls,
        }


class DiceEngine:
    def __init__(self, rng: Randomness) -> None:
        self.rng = rng

    # --- primitives ----------------------------------------------------------
    def roll_die(self, sides: int) -> int:
        return self.rng.roll(sides)

    def roll_d20(self, *, advantage: bool = False, disadvantage: bool = False) -> tuple[int, list[int]]:
        """Return (kept_face, all_faces). Advantage+disadvantage cancel to a straight roll."""
        if advantage and disadvantage:
            advantage = disadvantage = False
        if advantage or disadvantage:
            a, b = self.rng.roll(20), self.rng.roll(20)
            kept = max(a, b) if advantage else min(a, b)
            return kept, [a, b]
        face = self.rng.roll(20)
        return face, [face]

    def roll_bonus_dice(self, grants: list["BonusGrant"] | None) -> list[BonusDie]:
        """Roll each granted expression HERE. An effect declares a die; only this
        engine ever turns it into a number."""
        rolled: list[BonusDie] = []
        for grant in grants or []:
            sides = _parse_bonus_expression(grant.expression)
            if not sides:
                continue
            faces = [self.rng.roll(s) for s in sides]
            rolled.append(BonusDie(
                source=grant.source, label=grant.label, expression=grant.expression,
                rolls=faces, total=sum(faces),
            ))
        return rolled

    # --- resolutions ---------------------------------------------------------
    def resolve_ability_check(
        self, *, modifier: int, dc: int, ability: str = "", skill: str | None = None,
        proficient: bool = False, advantage: bool = False, disadvantage: bool = False,
        bonus_grants: list["BonusGrant"] | None = None,
    ) -> AbilityCheckResult:
        kept, all_rolls = self.roll_d20(advantage=advantage, disadvantage=disadvantage)
        bonus = self.roll_bonus_dice(bonus_grants)
        total = kept + modifier + sum(b.total for b in bonus)
        return AbilityCheckResult(
            natural_roll=kept, all_rolls=all_rolls, modifier=modifier, total=total,
            dc=dc, outcome="success" if total >= dc else "failure",
            advantage=advantage, disadvantage=disadvantage, bonus_dice=bonus,
            ability=ability, skill=skill, proficient=proficient,
        )

    def resolve_saving_throw(
        self, *, modifier: int, dc: int, ability: str = "",
        advantage: bool = False, disadvantage: bool = False,
        bonus_grants: list["BonusGrant"] | None = None,
    ) -> AbilityCheckResult:
        return self.resolve_ability_check(
            modifier=modifier, dc=dc, ability=ability, skill=None,
            advantage=advantage, disadvantage=disadvantage, bonus_grants=bonus_grants,
        )

    def resolve_attack(
        self, *, attack_modifier: int, target_ac: int,
        advantage: bool = False, disadvantage: bool = False,
        bonus_grants: list["BonusGrant"] | None = None,
    ) -> RollResult:
        kept, all_rolls = self.roll_d20(advantage=advantage, disadvantage=disadvantage)
        bonus = self.roll_bonus_dice(bonus_grants)
        total = kept + attack_modifier + sum(b.total for b in bonus)
        # Natural 20 always hits; natural 1 always misses (supported rule). A bonus
        # die cannot rescue a natural 1 — the d20 face decides, not the total.
        if kept == 20:
            hit = True
        elif kept == 1:
            hit = False
        else:
            hit = total >= target_ac
        return RollResult(
            natural_roll=kept, all_rolls=all_rolls, modifier=attack_modifier, total=total,
            dc=target_ac, outcome="success" if hit else "failure",
            advantage=advantage, disadvantage=disadvantage, bonus_dice=bonus,
        )

    def resolve_damage(self, *, dice: list[int], flat_modifier: int = 0) -> tuple[int, list[int]]:
        """`dice` is a list of die sizes, e.g. [8] for 1d8, [6, 6] for 2d6."""
        rolls = [self.rng.roll(sides) for sides in dice]
        return sum(rolls) + flat_modifier, rolls
