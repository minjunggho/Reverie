"""Phase 5 acceptance: normal messages mutate NO state and never execute an action;
`!` routes to the committed pipeline; classifier categories are honoured.
Covers §34 steps 9–11."""
from __future__ import annotations

from sqlalchemy import func, select

from app.discord_bridge import BridgeResult, DiscordBridge, InboundMessage
from app.models.character import Character
from app.models.enums import MessageCategory
from app.models.event import Event
from app.orchestration.router import MessageRouter
from tests.support.factories import build_world, start_session_with_scene


def _inbound(mid, content, author="disc-p1"):
    return InboundMessage(
        discord_message_id=mid, guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name="กี้", content=content,
    )


class RecordingPipeline:
    def __init__(self):
        self.calls = []

    async def handle(self, ctx, action) -> BridgeResult:
        self.calls.append((ctx, action))
        return BridgeResult(handled=True, category=MessageCategory.COMMITTED_ACTION,
                            state_mutated=True)


async def _event_count(db, campaign_id) -> int:
    async with db.session() as session:
        return (
            await session.execute(
                select(func.count(Event.id)).where(Event.campaign_id == campaign_id)
            )
        ).scalar_one()


async def test_normal_message_executes_no_action_and_mutates_nothing(db, provider):
    """§34 step 9: a normal planning message must not execute a committed action."""
    world = await build_world(db)
    await start_session_with_scene(db, world)
    pipeline = RecordingPipeline()
    bridge = DiscordBridge(db, router=MessageRouter(db, provider), pipeline=pipeline)

    async with db.session() as s:
        hp_before = (await s.get(Character, world.kael_id)).hp

    result = await bridge.handle_inbound(_inbound("n1", "กูว่าเราไปดูหน้าต่างดีไหม"))

    assert result.handled and result.state_mutated is False
    assert result.category == MessageCategory.OOC_DISCUSSION
    assert pipeline.calls == []                       # no committed action executed
    assert await _event_count(db, world.campaign_id) == 0  # no canonical events
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).hp == hp_before  # no state change


async def test_committed_message_routes_to_pipeline(db, provider):
    """§34 steps 10–11: a `!` message is a committed action routed to the pipeline."""
    world = await build_world(db)
    await start_session_with_scene(db, world)
    pipeline = RecordingPipeline()
    bridge = DiscordBridge(db, router=MessageRouter(db, provider), pipeline=pipeline)

    result = await bridge.handle_inbound(
        _inbound("c1", "! ผมค่อยๆ เดินไปดูตรงหน้าต่าง พยายามไม่ให้ยามเห็น")
    )
    assert result.category == MessageCategory.COMMITTED_ACTION
    assert len(pipeline.calls) == 1
    _, action = pipeline.calls[0]
    # Prefix stripped; Thai preserved verbatim.
    assert action.action_text == "ผมค่อยๆ เดินไปดูตรงหน้าต่าง พยายามไม่ให้ยามเห็น"


async def test_dm_question_gets_a_reply(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    bridge = DiscordBridge(db, router=MessageRouter(db, provider), pipeline=RecordingPipeline())
    result = await bridge.handle_inbound(_inbound("q1", "ในห้องนี้มีทางออกอื่นไหม"))
    assert result.category == MessageCategory.DM_QUESTION
    assert result.responses  # bot answers questions
    assert result.state_mutated is False


async def test_rules_question_classified(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    bridge = DiscordBridge(db, router=MessageRouter(db, provider), pipeline=RecordingPipeline())
    result = await bridge.handle_inbound(_inbound("q2", "อันนี้ต้องทอยเช็คไหม"))
    assert result.category == MessageCategory.RULES_QUESTION


async def test_non_committed_message_is_cached_and_deduped(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    bridge = DiscordBridge(db, router=MessageRouter(db, provider), pipeline=RecordingPipeline())
    first = await bridge.handle_inbound(_inbound("q3", "ในห้องนี้มีทางออกอื่นไหม"))
    second = await bridge.handle_inbound(_inbound("q3", "ในห้องนี้มีทางออกอื่นไหม"))
    assert not first.duplicate and second.duplicate
    # Cached response is returned on redelivery.
    assert second.responses and second.responses[0].content == first.responses[0].content
