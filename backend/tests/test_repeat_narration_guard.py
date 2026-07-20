"""Repeat-narration guard (issue #1 — "don't restate the last paragraph").

Two halves: a deterministic detector (`is_repeat_narration`) used for observability,
and the structural prevention — the previous turn's narration is carried into the next
turn's scene packet so the narrator CONTINUES the scene instead of repeating it. The
prevention lives in the structured context, not a system-prompt plea.
"""
from __future__ import annotations

from app.ai.narration_guard import is_repeat_narration
from app.core.randomness import SequenceRandomness
from app.discord_bridge import InboundMessage
from app.engine import build_bridge
from app.schemas.llm_io import Narration
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1"):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"rp{_n['v']}", guild_id="g", channel_id="chan-1",
        author_discord_id=author, author_display_name="กี้", content=content,
    )


# --- the deterministic detector ----------------------------------------------

def test_identical_paragraph_is_flagged():
    assert is_repeat_narration("ยามหันมามองแล้วเดินเข้ามา", "ยามหันมามองแล้วเดินเข้ามา")


def test_whitespace_and_case_differences_still_flag():
    assert is_repeat_narration("Abc  DEF\n ghi", "abc def ghi")


def test_substantively_different_narration_is_not_flagged():
    assert not is_repeat_narration(
        "ยามหันมามองแล้วเดินเข้ามา",
        "ประตูใต้ดินเลื่อนเปิดออก เผยบันไดหินที่ทอดลงไปในความมืด")


def test_no_previous_narration_is_never_a_repeat():
    assert not is_repeat_narration(None, "อะไรก็ตาม")
    assert not is_repeat_narration("", "อะไรก็ตาม")


# --- the structural prevention: last beat reaches the next packet -------------

async def test_previous_narration_is_carried_into_the_next_packet(db, provider):
    world = await build_world(db)          # AUTO dice mode -> resolves each turn
    await start_session_with_scene(db, world)

    seen: list[str] = []

    def _cap(messages, model):
        seen.append("\n".join(m.get("content", "") for m in messages))
        return Narration(text=f"ฉากที่ {len(seen)}: เงาบางอย่างเคลื่อนผ่านซอกหิน",
                         decision_prompt="จะทำอะไรต่อ?")

    provider.on("generate_dm_narration", _cap)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=12))

    await bridge.handle_inbound(_msg("! ผมมองไปรอบๆ"))
    await bridge.handle_inbound(_msg("! ผมมองไปรอบๆ อีกครั้ง"))

    assert len(seen) == 2
    # Turn 1 had nothing before it; turn 2 is handed turn 1's paragraph explicitly.
    assert "PREVIOUS_NARRATION" not in seen[0]
    assert "PREVIOUS_NARRATION" in seen[1]
    assert "ฉากที่ 1:" in seen[1]
