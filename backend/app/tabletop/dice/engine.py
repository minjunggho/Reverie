"""Authoritative server-side dice engine.

Every die is produced HERE, via an injected `Randomness`. The LLM has no reference
to this engine and no code path that produces a die, a modifier, or an outcome —
that is proven in the dice tests. Results are plain structured values the engine
turns into events.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.randomness import Randomness


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

    # --- resolutions ---------------------------------------------------------
    def resolve_ability_check(
        self, *, modifier: int, dc: int, ability: str = "", skill: str | None = None,
        proficient: bool = False, advantage: bool = False, disadvantage: bool = False,
    ) -> AbilityCheckResult:
        kept, all_rolls = self.roll_d20(advantage=advantage, disadvantage=disadvantage)
        total = kept + modifier
        return AbilityCheckResult(
            natural_roll=kept, all_rolls=all_rolls, modifier=modifier, total=total,
            dc=dc, outcome="success" if total >= dc else "failure",
            advantage=advantage, disadvantage=disadvantage,
            ability=ability, skill=skill, proficient=proficient,
        )

    def resolve_saving_throw(
        self, *, modifier: int, dc: int, ability: str = "",
        advantage: bool = False, disadvantage: bool = False,
    ) -> AbilityCheckResult:
        return self.resolve_ability_check(
            modifier=modifier, dc=dc, ability=ability, skill=None,
            advantage=advantage, disadvantage=disadvantage,
        )

    def resolve_attack(
        self, *, attack_modifier: int, target_ac: int,
        advantage: bool = False, disadvantage: bool = False,
    ) -> RollResult:
        kept, all_rolls = self.roll_d20(advantage=advantage, disadvantage=disadvantage)
        total = kept + attack_modifier
        # Natural 20 always hits; natural 1 always misses (supported rule).
        if kept == 20:
            hit = True
        elif kept == 1:
            hit = False
        else:
            hit = total >= target_ac
        return RollResult(
            natural_roll=kept, all_rolls=all_rolls, modifier=attack_modifier, total=total,
            dc=target_ac, outcome="success" if hit else "failure",
            advantage=advantage, disadvantage=disadvantage,
        )

    def resolve_damage(self, *, dice: list[int], flat_modifier: int = 0) -> tuple[int, list[int]]:
        """`dice` is a list of die sizes, e.g. [8] for 1d8, [6, 6] for 2d6."""
        rolls = [self.rng.roll(sides) for sides in dice]
        return sum(rolls) + flat_modifier, rolls
