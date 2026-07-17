"""CHECK_SETUP — the fiction-first pre-roll beat (issue #1, item D).

Invariants under test: no outcome/DC leak, no state commit before the roll, pending
action identity survives, cancel/replace/recovery remain safe and never double-roll
or double-commit, and hidden DM secrets never reach the setup context.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import InboundMessage
from app.engine import build_bridge
from app.models.campaign import Campaign
from app.models.enums import EventType
from app.models.event import Event
from app.models.scene import Scene
from app.presentation import MessageKind
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1"):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"cs{_n['v']}", guild_id="g", channel_id="chan-1",
        author_discord_id=author, author_display_name="กี้", content=content,
    )


async def _ritual_world(db):
    world = await build_world(db)
    session_id, scene_id = await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        campaign = await s.get(Campaign, world.campaign_id)
        campaign.config = {**campaign.config, "dice_mode": "PLAYER_CLICK"}
    return world, session_id, scene_id


async def test_check_setup_precedes_check_prompt_with_no_outcome_or_dc_leak(db, provider):
    world, _, _ = await _ritual_world(db)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))

    r = await bridge.handle_inbound(_msg("! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    assert r.responses[0].kind == MessageKind.CHECK_SETUP
    assert r.responses[1].kind == MessageKind.CHECK_PROMPT
    setup_text = r.responses[0].content
    assert "สำเร็จ" not in setup_text
    assert "ล้มเหลว" not in setup_text
    assert "DC" not in setup_text and "15" not in setup_text
    # Nothing rolled or committed yet.
    async with db.session() as s:
        count = (await s.execute(select(func.count(Event.id)).where(
            Event.event_type == EventType.ABILITY_CHECK_RESOLVED.value))).scalar_one()
        assert count == 0
    assert r.state_mutated is False


async def test_pending_action_identity_and_targets_survive_the_setup_beat(db, provider):
    world, _, scene_id = await _ritual_world(db)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))

    await bridge.handle_inbound(_msg("! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    async with db.session() as s:
        scene = await s.get(Scene, scene_id)
        pending = scene.pending_action
    assert pending is not None
    assert pending["kind"] == "check"
    assert pending["action_text"] == "ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"

    r = await bridge.handle_inbound(_msg("🎲 ทอย d20"))
    assert r.responses[0].kind == MessageKind.CHECK_RESOLUTION
    assert "16 + 5 = 21" in r.responses[0].data["roll_line"]


async def test_cancel_after_check_setup_commits_nothing(db, provider):
    world, _, scene_id = await _ritual_world(db)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))

    await bridge.handle_inbound(_msg("! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    r = await bridge.handle_inbound(_msg("ยกเลิก"))
    assert "ยังไม่มีอะไรเกิดขึ้น" in r.responses[0].content
    async with db.session() as s:
        assert (await s.get(Scene, scene_id)).pending_action is None
        count = (await s.execute(select(func.count(Event.id)).where(
            Event.event_type == EventType.ABILITY_CHECK_RESOLVED.value))).scalar_one()
        assert count == 0


async def test_new_action_after_check_setup_replaces_pending_safely(db, provider):
    world, _, _ = await _ritual_world(db)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16, 12]))

    await bridge.handle_inbound(_msg("! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    r = await bridge.handle_inbound(_msg("! ผมเดินไปเปิดประตู"))
    assert r.state_mutated is True and "outcome=success" in r.note


async def test_recovery_after_roll_never_rolls_or_commits_twice(db, provider):
    """A narration failure AFTER the roll must recover the factual result without
    re-rolling or re-applying the consequence (issue #1, item G)."""
    world, _, _ = await _ritual_world(db)
    provider.simulate_invalid("generate_dm_narration", 10)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))

    await bridge.handle_inbound(_msg("! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    r = await bridge.handle_inbound(_msg("🎲 ทอย d20"))
    assert r.state_mutated is True
    async with db.session() as s:
        checks = (await s.execute(select(Event).where(
            Event.event_type == EventType.ABILITY_CHECK_RESOLVED.value))).scalars().all()
        assert len(checks) == 1
        assert checks[0].mechanical_changes["natural_roll"] == 16


async def test_check_setup_never_receives_hidden_dm_secrets(db, provider):
    """The setup context is built from player-safe canon only; scene secrets/DC never
    enter its prompt (structural, not just prompt discipline)."""
    world, _, scene_id = await _ritual_world(db)
    async with db.unit_of_work() as s:
        scene = await s.get(Scene, scene_id)
        scene.allowed_clues = ["...ไม่ใช่ของมนุษย์"]

    captured = {}

    def _capture_setup(messages, model):
        # Only the USER content is the actual data payload — the system prompt is
        # instructions, not context, and legitimately mentions the word "DC" as a
        # prohibition.
        captured["blob"] = "\n".join(
            m.get("content", "") for m in messages if m.get("role") == "user"
        )
        from app.schemas.llm_io import CheckSetup
        return CheckSetup(text="เจ้าค่อยๆ เคลื่อนตัวเข้าใกล้ ยามยังไม่หันมา")

    provider.on("generate_check_setup", _capture_setup)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))
    await bridge.handle_inbound(_msg("! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))

    # DC never sent to the setup job (no "DC" marker at all — entity IDs are random
    # hex and may coincidentally contain digit substrings, so check the marker, not
    # a bare digit).
    assert "DC" not in captured["blob"]
    assert "dc_band" not in captured["blob"]
    assert "...ไม่ใช่ของมนุษย์" not in captured["blob"]   # allowed_clues never leak pre-roll
