"""NarrativePacing — an ENGINE-selected tier, never the LLM's free choice (issue #1).

`select_pacing()` is a pure function over known structured signals (resolution type,
consequence class, criticality, scene mode, whether a character hook connects, and
session/scene-transition flags). The narrator prompt only ever *follows* the tier it
is given; it never decides its own length.
"""
from __future__ import annotations

from app.models.enums import ConsequenceClass, ResolutionType, SceneMode, StrEnum


class NarrativePacing(StrEnum):
    QUICK = "QUICK"
    STANDARD = "STANDARD"
    DRAMATIC = "DRAMATIC"
    CINEMATIC = "CINEMATIC"


_DRAMATIC_CONSEQUENCES = {
    ConsequenceClass.FAILURE_WITH_CONSEQUENCE,
    ConsequenceClass.FAILURE_WITH_PROGRESS,
}


def select_pacing(
    *,
    resolution_type: ResolutionType | None = None,
    consequence_class: ConsequenceClass | None = None,
    is_saving_throw: bool = False,
    critical: bool = False,
    scene_mode: SceneMode | str | None = None,
    hook_connected: bool = False,
    is_session_opening: bool = False,
    is_major_scene_transition: bool = False,
) -> NarrativePacing:
    """Deterministic pacing selection. Every input is a fact already known to the
    engine BEFORE any prose is generated — the LLM supplies none of these signals."""
    if is_session_opening or is_major_scene_transition:
        return NarrativePacing.CINEMATIC

    if critical:
        return NarrativePacing.CINEMATIC

    if is_saving_throw and hook_connected:
        return NarrativePacing.CINEMATIC

    if hook_connected and consequence_class in _DRAMATIC_CONSEQUENCES:
        return NarrativePacing.CINEMATIC

    # Trivial/automatic resolutions with no meaningful consequence stay brief —
    # "opening an ordinary unlocked door" must never inflate to a scene.
    trivial_resolution = resolution_type in (
        ResolutionType.AUTOMATIC_SUCCESS, ResolutionType.AUTOMATIC_FAILURE,
    )
    no_consequence = consequence_class in (None, ConsequenceClass.SUCCESS)
    if trivial_resolution and no_consequence:
        return NarrativePacing.QUICK

    if (
        is_saving_throw
        or consequence_class in _DRAMATIC_CONSEQUENCES
        or scene_mode == SceneMode.COMBAT
    ):
        return NarrativePacing.DRAMATIC

    return NarrativePacing.STANDARD
