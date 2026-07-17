"""Time passes when the party goes in circles.

`WorldClockService.advance_time` is the one path that moves in-world time, and it fires
threats, faction beats, rumor spread, and scheduled events. Its only callers were
travel, rest, and an LLM-proposed delta. Travel and rest already charge for productive
play — the uncovered case was exactly the one the brief names: a party standing in one
room repeating low-progress actions, where nothing advanced the clock and the
consequence engine could never fire. The world waited forever
(docs/progression-audit.md, RC4).

So this module answers one narrow question: what does a STALLED turn cost?

A turn that moves the story costs nothing here. That is deliberate, and it is not
timidity — the clock's unit is MINUTES while a conversational beat is seconds, so any
per-turn tick is already a rounding-up. Charging it every turn would mean a Minor
Illusion (1 minute) could never survive the action after the one that cast it: effects
expire on `now >= started + duration`, so even a 1-minute tick kills it. Productive play
gets its time from travel and rest, which is where the hours actually go. Circling gets
charged, because that is the case where the world must stop politely waiting.

COMBAT is exempt at any stall level. Combat is rounds of six seconds; charging minutes
per swing would make a two-minute fight take an hour and fire the world's scheduled
beats mid-initiative.
"""
from __future__ import annotations

from app.models.enums import SceneMode

# What one turn of visible circling costs, by scene mode. Big enough that the world's
# scheduled beats actually come due, small enough that a stalled evening is not a lost
# week.
_STALL_MINUTES: dict[str, int] = {
    SceneMode.SOCIAL.value: 5,
    SceneMode.EXPLORATION.value: 10,
    SceneMode.DOWNTIME.value: 15,
    SceneMode.COMBAT.value: 0,   # rounds, not minutes — see module docstring
}
_DEFAULT_STALL_MINUTES = 5


def minutes_for_turn(*, scene_mode: str | None, stalled: bool) -> int:
    """In-world minutes this committed action costs. 0 means the clock does not move.

    Only a stalled turn costs time here; see the module docstring for why a per-turn
    tick is the wrong instrument.
    """
    if not stalled:
        return 0
    mode = scene_mode or SceneMode.EXPLORATION.value
    if mode == SceneMode.COMBAT.value:
        return 0
    return _STALL_MINUTES.get(mode, _DEFAULT_STALL_MINUTES)
