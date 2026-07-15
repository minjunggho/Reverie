"""Phase 4 acceptance: idempotency dedupe, identity resolution, `!` detection, and
the per-session serialized queue."""
from __future__ import annotations

import asyncio

from app.discord_bridge import BridgeResult, DiscordBridge, InboundMessage
from app.models.enums import CommitmentSource, MessageCategory
from app.orchestration.commitment import detect_commitment
from app.orchestration.context import ResolvedContext
from app.orchestration.serializer import SessionSerializer
from tests.support.factories import build_world, start_session_with_scene


def _inbound(mid: str, author: str = "disc-p1", content: str = "! ผมเปิดประตู") -> InboundMessage:
    return InboundMessage(
        discord_message_id=mid, guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name="กี้", content=content,
    )


class RecordingPipeline:
    def __init__(self) -> None:
        self.calls: list[ResolvedContext] = []

    async def handle(self, ctx, action) -> BridgeResult:
        self.calls.append(ctx)
        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION,
            state_mutated=True, note=f"handled: {action.action_text}",
        )


def test_commitment_detection():
    assert detect_commitment(_inbound("m", content="! ย่องไป")).commitment_source == (
        CommitmentSource.EXPLICIT_PREFIX
    )
    # Marker stripped, Thai preserved verbatim (including internal spaces).
    c = detect_commitment(_inbound("m", content="!   ผมค่อยๆ เดินไปดู"))
    assert c is not None and c.action_text == "ผมค่อยๆ เดินไปดู"
    # Leading whitespace before the marker still counts.
    assert detect_commitment(_inbound("m", content="   ! วิ่ง")) is not None
    # A normal message is NOT a commitment.
    assert detect_commitment(_inbound("m", content="เราไปดูหน้าต่างดีไหม")) is None
    # A plain `!` action is not speech.
    assert detect_commitment(_inbound("m", content="! ย่องไป")).is_speech is False


def test_commitment_speech_detection():
    # `!"..."` is SPEECH: quotes stripped, words verbatim, is_speech True.
    c = detect_commitment(_inbound("m", content='!"เปิดประตูให้หน่อย"'))
    assert c is not None and c.is_speech is True and c.action_text == "เปิดประตูให้หน่อย"
    # A space after the marker still counts, and curly quotes work too (IME/autocorrect).
    c = detect_commitment(_inbound("m", content='! “หยุดอยู่ตรงนั้น”'))
    assert c is not None and c.is_speech is True and c.action_text == "หยุดอยู่ตรงนั้น"
    # A partial/embedded quote is NOT speech — it stays a physical action verbatim.
    c = detect_commitment(_inbound("m", content='!ผลักประตูแล้วตะโกน "หยุด"'))
    assert c is not None and c.is_speech is False
    assert c.action_text == 'ผลักประตูแล้วตะโกน "หยุด"'


async def test_bridge_resolves_member_and_character(db):
    world = await build_world(db)
    session_id, _ = await start_session_with_scene(db, world)
    pipeline = RecordingPipeline()
    bridge = DiscordBridge(db, pipeline=pipeline)

    result = await bridge.handle_inbound(_inbound("msg-1", author="disc-p1"))
    assert result.handled and result.category == MessageCategory.COMMITTED_ACTION
    assert len(pipeline.calls) == 1
    ctx = pipeline.calls[0]
    assert ctx.member_id == world.p1_member_id
    assert ctx.character_id == world.kael_id
    assert ctx.session_id == session_id


async def test_bridge_dedupes_duplicate_message(db):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    pipeline = RecordingPipeline()
    bridge = DiscordBridge(db, pipeline=pipeline)

    first = await bridge.handle_inbound(_inbound("dup-1"))
    second = await bridge.handle_inbound(_inbound("dup-1"))
    assert first.handled and not first.duplicate
    assert second.duplicate is True
    # The committed pipeline ran exactly once despite two deliveries.
    assert len(pipeline.calls) == 1


async def test_bridge_non_member_gets_notice(db):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    bridge = DiscordBridge(db, pipeline=RecordingPipeline())
    result = await bridge.handle_inbound(_inbound("m-x", author="stranger-999"))
    assert result.handled
    assert result.responses and "สมาชิก" in result.responses[0].content


async def test_bridge_ignores_unknown_channel(db):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    bridge = DiscordBridge(db, pipeline=RecordingPipeline())
    inbound = InboundMessage(
        discord_message_id="m-o", guild_id="guild-1", channel_id="other-channel",
        author_discord_id="disc-p1", author_display_name="กี้", content="! อะไรสักอย่าง",
    )
    result = await bridge.handle_inbound(inbound)
    assert result.handled is False


async def test_bridge_committed_without_session_is_blocked(db):
    world = await build_world(db)  # no session started
    bridge = DiscordBridge(db, pipeline=RecordingPipeline())
    result = await bridge.handle_inbound(_inbound("m-ns"))
    assert result.handled
    assert result.responses and "เซสชัน" in result.responses[0].content


async def test_serializer_prevents_interleaving():
    serializer = SessionSerializer()
    order: list[str] = []

    async def make(n: int) -> int:
        async def work() -> int:
            order.append(f"s{n}")
            await asyncio.sleep(0.01)
            order.append(f"e{n}")
            return n

        return await serializer.run("sess", work)

    await asyncio.gather(make(1), make(2))
    # Each critical section is uninterrupted: end immediately follows its own start.
    assert order.index("e1") == order.index("s1") + 1
    assert order.index("e2") == order.index("s2") + 1
