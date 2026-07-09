"""Phase 6 acceptance: the four Thai interpretation cases clarify or not correctly,
and a pending action is persisted and resumed."""
from __future__ import annotations

from sqlalchemy import func, select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import DiscordBridge, InboundMessage
from app.engine import build_bridge
from app.models.enums import ActivePlayState, MessageCategory
from app.models.event import Event
from app.models.scene import Scene
from app.models.session import Session
from tests.support.factories import build_world, start_session_with_scene


def _inbound(mid, content, author="disc-p1"):
    return InboundMessage(
        discord_message_id=mid, guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name="กี้", content=content,
    )


async def _bridge(db, provider) -> DiscordBridge:
    # Give plenty of deterministic rolls for any resolution that proceeds.
    return build_bridge(db, provider=provider, rng=SequenceRandomness(default=14))


async def _event_count(db, campaign_id):
    async with db.session() as s:
        return (
            await s.execute(select(func.count(Event.id)).where(Event.campaign_id == campaign_id))
        ).scalar_one()


async def test_ambiguous_action_requires_clarification(db, provider):
    world = await build_world(db)
    session_id, scene_id = await start_session_with_scene(db, world)
    bridge = await _bridge(db, provider)

    result = await bridge.handle_inbound(_inbound("a1", "! ผมจัดการยาม"))
    assert result.responses and "จัดการยามยังไง" in result.responses[0].content
    assert result.state_mutated is False
    # No canonical events yet; pending action persisted; state set to CLARIFICATION_REQUIRED.
    assert await _event_count(db, world.campaign_id) == 0
    async with db.session() as s:
        scene = await s.get(Scene, scene_id)
        assert scene.pending_action is not None
        assert scene.pending_action["member_id"] == world.p1_member_id
        sess = await s.get(Session, session_id)
        assert sess.active_play_state == ActivePlayState.CLARIFICATION_REQUIRED.value


async def test_clear_action_does_not_clarify(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    bridge = await _bridge(db, provider)
    result = await bridge.handle_inbound(
        _inbound("a2", "! ผมค่อยๆ ย่องไปดูตรงหน้าต่าง พยายามไม่ให้ยามเห็น")
    )
    # Proceeds to a committed resolution (state mutated, events recorded).
    assert result.state_mutated is True
    assert await _event_count(db, world.campaign_id) >= 2


async def test_open_door_auto_resolves_without_clarification(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    bridge = await _bridge(db, provider)
    result = await bridge.handle_inbound(_inbound("a3", "! ผมเดินไปเปิดประตู"))
    assert result.state_mutated is True
    assert "outcome=success" in result.note


async def test_inspect_corpse_no_clarification(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    bridge = await _bridge(db, provider)
    result = await bridge.handle_inbound(_inbound("a4", "! กูลอง inspect ศพ"))
    assert result.state_mutated is True


async def test_pending_action_is_resumed_by_next_message(db, provider):
    world = await build_world(db)
    session_id, scene_id = await start_session_with_scene(db, world)
    bridge = await _bridge(db, provider)

    # Ambiguous -> clarification.
    await bridge.handle_inbound(_inbound("c1", "! ผมจัดการยาม"))
    # The committing player's next message resolves it (need not start with `!`).
    result = await bridge.handle_inbound(_inbound("c2", "แอบเลี่ยงผ่านไปเงียบๆ ไม่ให้ยามเห็น"))

    assert result.state_mutated is True  # now it proceeds to resolution
    async with db.session() as s:
        scene = await s.get(Scene, scene_id)
        assert scene.pending_action is None  # cleared
        sess = await s.get(Session, session_id)
        assert sess.active_play_state == ActivePlayState.TABLE_OPEN.value
