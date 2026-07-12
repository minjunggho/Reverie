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


def day_segment_th(minutes: int) -> str:
    """Thai day segment for narration/time headers (engine-owned; the narrator must
    never claim sunset while the clock says morning)."""
    hh = (minutes % DAY) // HOUR
    if hh < 6:
        return "กลางดึก"
    if hh < 9:
        return "เช้าตรู่"
    if hh < 12:
        return "สาย"
    if hh < 16:
        return "บ่าย"
    if hh < 19:
        return "เย็น"
    if hh < 22:
        return "หัวค่ำ"
    return "ดึก"


def format_game_time_th(minutes: int) -> str:
    """Full Thai-facing header line, e.g. 'วันที่ 3 · 17:40 · เย็น'."""
    day = minutes // DAY + 1
    rem = minutes % DAY
    return f"วันที่ {day} · {rem // HOUR:02d}:{rem % HOUR:02d} · {day_segment_th(minutes)}"
