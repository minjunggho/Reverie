"""Phase 7 acceptance (dice): the engine owns every die and modifier; the LLM has no
path to an authoritative roll."""
from __future__ import annotations

import inspect

import pytest

from app.ai.llm.base import LLMProvider
from app.core.randomness import SequenceRandomness
from app.tabletop.adjudication import check_modifier, resolve_dc
from app.tabletop.dice import DiceEngine
from app.tabletop.rules import ability_modifier, proficiency_bonus_for_level


def test_dice_use_injected_randomness():
    dice = DiceEngine(SequenceRandomness([14, 3, 20]))
    assert dice.roll_die(20) == 14
    assert dice.roll_die(20) == 3
    assert dice.roll_die(20) == 20


def test_ability_check_resolution_math():
    dice = DiceEngine(SequenceRandomness([14]))
    res = dice.resolve_ability_check(modifier=5, dc=15, ability="dex", skill="stealth", proficient=True)
    assert res.natural_roll == 14
    assert res.total == 19
    assert res.outcome == "success"


def test_ability_check_failure():
    dice = DiceEngine(SequenceRandomness([3]))
    res = dice.resolve_ability_check(modifier=5, dc=15, ability="dex", skill="stealth")
    assert res.total == 8 and res.outcome == "failure"


def test_advantage_takes_higher_disadvantage_lower():
    dice = DiceEngine(SequenceRandomness([7, 18]))
    kept, faces = dice.roll_d20(advantage=True)
    assert kept == 18 and faces == [7, 18]
    dice = DiceEngine(SequenceRandomness([7, 18]))
    kept, faces = dice.roll_d20(disadvantage=True)
    assert kept == 7


def test_modifier_calculation_from_character(db):
    # Pure arithmetic checks against the rules subset.
    assert ability_modifier(16) == 3
    assert ability_modifier(10) == 0
    assert ability_modifier(8) == -1
    assert proficiency_bonus_for_level(1) == 2
    assert proficiency_bonus_for_level(5) == 3


def test_dc_band_clamping():
    from app.models.enums import DifficultyBand

    assert resolve_dc(DifficultyBand.MEDIUM) == 15
    assert resolve_dc(DifficultyBand.HARD) == 20
    assert resolve_dc(None) == 15  # default when the AI proposes nothing


def test_llm_provider_has_no_dice_capability():
    """Structural guarantee: the provider abstraction exposes no roll/dice method,
    and no convenience method returns a die. The only randomness lives in DiceEngine."""
    method_names = {name for name, _ in inspect.getmembers(LLMProvider, inspect.isfunction)}
    for banned in ("roll", "roll_die", "roll_d20", "random", "randint"):
        assert banned not in method_names
    # The dice engine, by contrast, requires a Randomness source it cannot fabricate.
    with pytest.raises(TypeError):
        DiceEngine()  # type: ignore[call-arg]
