"""select_pacing() — the ENGINE, not the LLM, chooses the narrative tier (issue #1)."""
from __future__ import annotations

from app.ai.pacing import NarrativePacing, select_pacing
from app.models.enums import ConsequenceClass, ResolutionType, SceneMode


def test_ordinary_unlocked_door_is_quick():
    pacing = select_pacing(
        resolution_type=ResolutionType.AUTOMATIC_SUCCESS,
        consequence_class=ConsequenceClass.SUCCESS,
    )
    assert pacing == NarrativePacing.QUICK


def test_standard_investigation_check_is_standard():
    pacing = select_pacing(
        resolution_type=ResolutionType.ABILITY_CHECK,
        consequence_class=ConsequenceClass.SUCCESS,
        scene_mode=SceneMode.EXPLORATION,
    )
    assert pacing == NarrativePacing.STANDARD


def test_failed_stealth_that_alerts_an_enemy_is_dramatic():
    pacing = select_pacing(
        resolution_type=ResolutionType.ABILITY_CHECK,
        consequence_class=ConsequenceClass.FAILURE_WITH_CONSEQUENCE,
        scene_mode=SceneMode.EXPLORATION,
    )
    assert pacing == NarrativePacing.DRAMATIC


def test_saving_throw_connected_to_established_trauma_is_cinematic():
    pacing = select_pacing(
        resolution_type=ResolutionType.SAVING_THROW,
        consequence_class=ConsequenceClass.FAILURE,
        is_saving_throw=True,
        hook_connected=True,
    )
    assert pacing == NarrativePacing.CINEMATIC


def test_saving_throw_without_a_hook_connection_is_only_dramatic():
    """A saving throw is always at least DRAMATIC, but CINEMATIC requires an actual
    connection to established character history — never just "it's a save"."""
    pacing = select_pacing(
        resolution_type=ResolutionType.SAVING_THROW,
        consequence_class=ConsequenceClass.FAILURE,
        is_saving_throw=True,
        hook_connected=False,
    )
    assert pacing == NarrativePacing.DRAMATIC


def test_session_1_opening_is_cinematic():
    pacing = select_pacing(is_session_opening=True)
    assert pacing == NarrativePacing.CINEMATIC


def test_critical_result_is_cinematic():
    pacing = select_pacing(
        resolution_type=ResolutionType.ABILITY_CHECK,
        consequence_class=ConsequenceClass.SUCCESS,
        critical=True,
    )
    assert pacing == NarrativePacing.CINEMATIC


def test_combat_scene_mode_is_at_least_dramatic():
    pacing = select_pacing(
        resolution_type=ResolutionType.ATTACK,
        consequence_class=ConsequenceClass.SUCCESS,
        scene_mode=SceneMode.COMBAT,
    )
    assert pacing == NarrativePacing.DRAMATIC
