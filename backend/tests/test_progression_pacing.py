"""The world does not wait while the party talks.

`advance_time` is the one path that moves in-world time, and it is what fires threats,
faction beats, rumor spread, and scheduled events. Its only callers were travel, rest,
and an LLM-proposed delta — so a party standing in one room talking froze the world
entirely. The whole world-consequence engine was gated behind the action a stuck party
was not taking (docs/progression-audit.md, RC4).

Nothing evaluated whether a turn accomplished anything either, so a scene ran forever
with no cue for the world to lean in (RC5).
"""
from __future__ import annotations

from app.models.enums import SceneMode
from app.models.scene import Scene
from app.services.scenes.stall_service import (
    STALL_THRESHOLD,
    StallService,
    TurnProgress,
)
from app.world.turn_clock import minutes_for_turn


# --- circling costs time; productive play does not ------------------------------

def test_circling_advances_the_world_clock():
    """The core fix: a party going in circles no longer freezes the world."""
    for mode in (SceneMode.SOCIAL.value, SceneMode.EXPLORATION.value, None):
        assert minutes_for_turn(scene_mode=mode, stalled=True) > 0


def test_a_productive_turn_costs_no_clock_time():
    """Deliberate, and not timidity. The clock's unit is MINUTES while a turn is
    seconds, so a per-turn tick is already rounding up — and it would mean a 1-minute
    Minor Illusion could never survive the action after the one that cast it (effects
    expire on `now >= started + duration`). Productive play gets its time from travel
    and rest, which is where the hours actually go.
    """
    for mode in (SceneMode.SOCIAL.value, SceneMode.EXPLORATION.value,
                 SceneMode.DOWNTIME.value, None):
        assert minutes_for_turn(scene_mode=mode, stalled=False) == 0


def test_combat_does_not_burn_minutes_per_swing():
    """Combat is rounds of six seconds. Charging minutes per action would make a
    two-minute fight take an hour and fire scheduled world beats mid-initiative.
    Stalling must not punch a hole in that exemption either."""
    assert minutes_for_turn(scene_mode=SceneMode.COMBAT.value, stalled=False) == 0
    assert minutes_for_turn(scene_mode=SceneMode.COMBAT.value, stalled=True) == 0


# --- the engine knows when nothing is happening --------------------------------

def _scene() -> Scene:
    return Scene(session_id="s1", mode=SceneMode.SOCIAL.value, low_progress_turns=0)


def test_dead_turns_accumulate_and_trip_the_stall():
    scene = _scene()
    for _ in range(STALL_THRESHOLD - 1):
        state = StallService.record(scene, TurnProgress())
        assert not state.stalled, "the world must not lean in over ordinary conversation"
    state = StallService.record(scene, TurnProgress())
    assert state.stalled
    assert state.low_progress_turns == STALL_THRESHOLD


def test_any_real_progress_resets_the_counter():
    scene = _scene()
    for _ in range(STALL_THRESHOLD):
        StallService.record(scene, TurnProgress())
    assert StallService.state(scene).stalled

    state = StallService.record(scene, TurnProgress(clue_opened=True))
    assert not state.stalled and state.low_progress_turns == 0


def test_each_kind_of_progress_counts():
    """Progress is decided from committed state, never the narrator's opinion."""
    for kwargs in ({"clue_opened": True}, {"objective_moved": True},
                   {"chapter_moved": True}, {"travelled": True},
                   {"world_changed": True}, {"secret_revealed": True}):
        assert TurnProgress(**kwargs).made_progress, kwargs
    assert not TurnProgress().made_progress


def test_the_pacing_signal_is_silent_until_the_party_is_actually_circling():
    scene = _scene()
    StallService.record(scene, TurnProgress())
    assert StallService.state(scene).as_block() == ""
    for _ in range(STALL_THRESHOLD):
        StallService.record(scene, TurnProgress())
    assert "PACING" in StallService.state(scene).as_block()


def test_the_pacing_signal_never_leaks_dm_planning_material():
    """SceneContext.pressure_block carries threats' next_action and progress — DM
    material. Narration produces player-facing prose, so the stall signal must push the
    scene without naming what the DM is planning."""
    scene = _scene()
    for _ in range(STALL_THRESHOLD):
        StallService.record(scene, TurnProgress())
    block = StallService.state(scene).as_block()
    assert "ACTIVE_PRESSURE" not in block and "progress" not in block


def test_a_scene_without_state_never_stalls():
    """Turns outside a scene (no scene row) must not crash or invent a stall."""
    state = StallService.record(None, TurnProgress())
    assert not state.stalled and state.low_progress_turns == 0
