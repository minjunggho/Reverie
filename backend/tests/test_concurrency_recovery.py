"""§31 concurrency + §32 error-recovery acceptance.

- duplicate Discord message deduped (also in Phase 4)
- two near-simultaneous committed actions serialized (no corruption; both commit)
- optimistic version conflict raises ConflictError
- invalid LLM structured output -> safe fallback (interpreter -> clarification)
- LLM failure on classify -> UNKNOWN fallback, no crash
- narration failure after commit does not re-execute (also in Phase 8)
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.core.errors import ConflictError
from app.core.randomness import SequenceRandomness
from app.discord_bridge import DiscordBridge, InboundMessage
from app.engine import build_bridge
from app.models.enums import EventType
from app.models.event import Event
from app.models.session import Session
from app.services.concurrency import guarded_version_update
from tests.support.factories import build_world, start_session_with_scene


def _msg(mid, content, author="disc-p1"):
    return InboundMessage(
        discord_message_id=mid, guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name="x", content=content,
    )


@pytest_asyncio.fixture
async def file_db(tmp_path):
    """A file-backed SQLite DB so multiple connections allow true concurrency."""
    from app.db.session import Database

    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/reverie_test.sqlite3"
    database = Database(url)
    await database.create_all()
    try:
        yield database
    finally:
        await database.dispose()


async def _check_count(db, campaign_id):
    async with db.session() as s:
        return (
            await s.execute(
                select(func.count(Event.id)).where(
                    Event.campaign_id == campaign_id,
                    Event.event_type == EventType.ABILITY_CHECK_RESOLVED.value,
                )
            )
        ).scalar_one()


async def test_two_simultaneous_committed_actions_are_serialized(file_db, provider):
    world = await build_world(file_db)
    await start_session_with_scene(file_db, world)
    # Two rolls queued; the serializer guarantees they're consumed one action at a time.
    bridge = build_bridge(file_db, provider=provider, rng=SequenceRandomness([16, 16]))

    r1, r2 = await asyncio.gather(
        bridge.handle_inbound(_msg("s-a", "! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น", author="disc-p1")),
        bridge.handle_inbound(_msg("s-b", "! ผมย่องไปดูประตู ไม่ให้ยามเห็น", author="disc-p2")),
    )
    assert r1.state_mutated and r2.state_mutated
    # Both actions committed exactly one check each — no lost update, no corruption.
    assert await _check_count(file_db, world.campaign_id) == 2


async def test_duplicate_committed_action_runs_once(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))
    first = await bridge.handle_inbound(_msg("d1", "! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    second = await bridge.handle_inbound(_msg("d1", "! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    assert first.state_mutated and second.duplicate
    assert await _check_count(db, world.campaign_id) == 1  # not double-applied


async def test_optimistic_version_conflict_raises(db):
    world = await build_world(db)
    session_id, _ = await start_session_with_scene(db, world)
    async with db.session() as s:
        current = (await s.get(Session, session_id)).version

    # A stale writer (wrong expected version) must lose.
    with pytest.raises(ConflictError):
        async with db.unit_of_work() as s:
            await guarded_version_update(s, Session, session_id, current - 1, status="CLOSING")

    # The correct expected version succeeds and bumps the version.
    async with db.unit_of_work() as s:
        new_version = await guarded_version_update(s, Session, session_id, current)
    assert new_version == current + 1


async def test_invalid_interpretation_output_falls_back_to_clarification(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    provider.simulate_invalid("interpret_committed_action", 10)  # never returns valid
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))
    result = await bridge.handle_inbound(_msg("iv", "! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    # Fallback interpretation has confidence 0 + missing info -> engine asks to clarify.
    assert result.state_mutated is False
    assert result.responses  # a clarification question was asked
    assert await _check_count(db, world.campaign_id) == 0  # nothing rolled/committed


async def test_classifier_failure_falls_back_to_unknown(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    provider.simulate_invalid("classify_table_message", 10)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))
    result = await bridge.handle_inbound(_msg("cf", "อะไรสักอย่างที่งงๆ"))
    from app.models.enums import MessageCategory

    assert result.handled and result.category == MessageCategory.UNKNOWN
    assert result.state_mutated is False
