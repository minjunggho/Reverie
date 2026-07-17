"""A DC answers "how hard is this, here, now" — not "which rung of the ladder".

Before this, `resolve_dc` was a dict lookup: every check landed on 5/10/15/20/25/30,
and in practice on 10/15/20. The same lock was DC 15 in daylight and in a storm; the
innkeeper who owed you his life was exactly as hard to persuade as a stranger.

The band still carries the task's intrinsic difficulty. What these tests pin is the
composition around it: factors from real state, factors named (never priced) by the
adjudicator, a cap so the band stays dominant, and an explanation the table can read.
"""
from __future__ import annotations

import pytest

from app.models.enums import DifficultyBand
from app.tabletop.adjudication import (
    DC_CEILING,
    DC_FLOOR,
    MAX_TOTAL_SWING,
    SITUATIONAL_FACTORS,
    DCFactor,
    compose_dc,
    factors_from_keys,
)


# --- the core complaint: DCs are no longer only 10/15/20 ------------------------

def test_a_composed_dc_is_not_confined_to_the_ladder():
    """The whole point: 17 and 13 are now reachable values."""
    harder = compose_dc(DifficultyBand.MEDIUM,
                        [DCFactor("x", +2, "มืด", "proposed")])
    easier = compose_dc(DifficultyBand.MEDIUM,
                        [DCFactor("y", -2, "มีเครื่องมือ", "proposed")])
    assert harder.total == 17
    assert easier.total == 13


def test_the_band_alone_still_gives_the_old_rung():
    """No factors = the previous behaviour, unchanged."""
    for band, expected in [(DifficultyBand.VERY_EASY, 5), (DifficultyBand.EASY, 10),
                           (DifficultyBand.MEDIUM, 15), (DifficultyBand.HARD, 20),
                           (DifficultyBand.VERY_HARD, 25),
                           (DifficultyBand.NEARLY_IMPOSSIBLE, 30)]:
        composed = compose_dc(band, [])
        assert composed.total == expected == composed.base


def test_a_missing_band_defaults_to_medium():
    assert compose_dc(None, []).total == 15


# --- the band stays dominant -----------------------------------------------------

def test_the_total_swing_is_capped():
    """A pile of small factors must not silently turn Medium into Nearly Impossible."""
    piled = [DCFactor(f"f{i}", +3, "ยาก", "proposed") for i in range(6)]   # +18 raw
    composed = compose_dc(DifficultyBand.MEDIUM, piled)
    assert composed.swing == MAX_TOTAL_SWING
    assert composed.total == 15 + MAX_TOTAL_SWING
    assert composed.capped is True


def test_the_cap_applies_in_the_easy_direction_too():
    piled = [DCFactor(f"f{i}", -3, "ง่าย", "proposed") for i in range(6)]
    composed = compose_dc(DifficultyBand.MEDIUM, piled)
    assert composed.swing == -MAX_TOTAL_SWING
    assert composed.capped is True


def test_an_uncapped_swing_is_not_flagged():
    composed = compose_dc(DifficultyBand.MEDIUM,
                          [DCFactor("a", +2, "x", "engine")])
    assert composed.capped is False
    assert composed.swing == 2


@pytest.mark.parametrize("band,factors,expected", [
    (DifficultyBand.VERY_EASY, [DCFactor("a", -3, "x", "engine")], DC_FLOOR),
    (DifficultyBand.NEARLY_IMPOSSIBLE, [DCFactor("a", +3, "x", "engine")], DC_CEILING),
])
def test_the_dc_is_clamped_to_the_playable_range(band, factors, expected):
    assert compose_dc(band, factors).total == expected


# --- the model names factors; it never prices them --------------------------------

def test_an_unknown_factor_key_is_dropped_not_guessed_at():
    """The model cannot invent a factor — an unrecognised key contributes nothing."""
    factors = factors_from_keys(["target_distracted", "the_vibes_are_off"],
                                skill="stealth")
    assert [f.key for f in factors] == ["target_distracted"]


def test_a_factors_delta_comes_from_the_engine_table():
    """Whatever the model says, the weight is the one the rules declare."""
    factors = factors_from_keys(["target_distracted"], skill="stealth")
    assert factors[0].delta == SITUATIONAL_FACTORS["target_distracted"].delta == -3
    assert factors[0].source == "proposed"


def test_a_factor_is_ignored_for_a_skill_it_cannot_touch():
    """Darkness has no business modifying Persuasion."""
    assert factors_from_keys(["darkness"], skill="persuasion") == []
    assert [f.key for f in factors_from_keys(["darkness"], skill="perception")] == [
        "darkness"]


def test_a_repeated_factor_is_only_counted_once():
    factors = factors_from_keys(["time_pressure", "time_pressure"], skill="stealth")
    assert len(factors) == 1


def test_factor_keys_are_normalised():
    factors = factors_from_keys(["  TARGET_ALERT  "], skill="stealth")
    assert [f.key for f in factors] == ["target_alert"]


def test_no_factor_alone_can_jump_a_whole_band():
    """Every declared delta is smaller than the 5-point gap between rungs, so a single
    factor shades a difficulty rather than replacing the band's judgement."""
    for key, definition in SITUATIONAL_FACTORS.items():
        assert 0 < abs(definition.delta) < 5, key


def test_every_vocabulary_entry_is_keyed_by_its_own_name():
    for key, definition in SITUATIONAL_FACTORS.items():
        assert key == definition.key
        assert definition.label_th, key


# --- the explanation --------------------------------------------------------------

def test_the_explanation_names_the_base_and_every_factor():
    composed = compose_dc(DifficultyBand.MEDIUM, [
        DCFactor("relationship_suspicious", +3, "เขาระแวงเจ้า", "engine"),
        DCFactor("crowded", -2, "ผู้คนพลุกพล่านช่วยกลบ", "proposed"),
    ])
    text = composed.explain_th()
    assert "DC 15 ฐาน" in text
    assert "+3 เขาระแวงเจ้า" in text
    assert "-2 ผู้คนพลุกพล่านช่วยกลบ" in text
    assert composed.total == 16


def test_an_unmodified_dc_explains_itself_plainly():
    assert compose_dc(DifficultyBand.HARD, []).explain_th() == "DC 20 ฐาน"


def test_the_composition_serialises_for_the_event_log():
    composed = compose_dc(DifficultyBand.MEDIUM,
                          [DCFactor("darkness", +3, "มืด", "proposed")])
    data = composed.as_dict()
    assert data["base"] == 15 and data["total"] == 18 and data["swing"] == 3
    assert data["factors"][0] == {"key": "darkness", "delta": 3, "label_th": "มืด",
                                  "source": "proposed"}
