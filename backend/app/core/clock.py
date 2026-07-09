"""Time helpers.

- **Real time** is wall-clock UTC (for `Event.real_time`).
- **Game time** is an integer number of in-world minutes since the campaign epoch
  (Campaign.current_game_time). Deterministic, engine-owned, never invented by the
  LLM. Keeping it as an int keeps arithmetic and scheduling trivial and portable.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Game-time unit conversions (in-world minutes).
MINUTE = 1
HOUR = 60
DAY = 24 * HOUR


def format_game_time(minutes: int) -> str:
    """Human-readable in-world clock, e.g. 'Day 4, 02:00'."""
    day = minutes // DAY + 1
    rem = minutes % DAY
    hh = rem // HOUR
    mm = rem % HOUR
    return f"Day {day}, {hh:02d}:{mm:02d}"
