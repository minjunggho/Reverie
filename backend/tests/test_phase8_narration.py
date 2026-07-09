"""Phase 8 acceptance: Thai narration is built ONLY from the committed result, cannot
alter state, and a narration failure after commit never re-executes the action
(the §32 critical invariant)."""
from __future__ import annotations

import re

from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import InboundMessage
from app.engine import build_bridge
from app.models.character import Character
from app.models.enums import EventType
from app.models.event import Event

THAI = re.compile(r"[฀-๿]")


def _inbound(mid, content, author="disc-p1"):
    return InboundMessage(
        discord_message_id=mid, guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name="กี้", content=content,
    )


async def _check_events(db, campaign_id):
    async with db.session() as s:
        return list(
            (
                await s.execute(
                    select(Event).where(
                        Event.campaign_id == campaign_id,
                        Event.event_type == EventType.ABILITY_CHECK_RESOLVED.value,
                    )
                )
            ).scalars()
        )


async def test_success_narration_is_thai_and_from_committed_result(db, provider):
    from tests.support.factories import build_world, start_session_with_scene

    world = await build_world(db)
    await start_session_with_scene(db, world)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([18]))
    result = await bridge.handle_inbound(_inbound("n1", "! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    assert result.responses
    text = result.responses[0].content
    assert THAI.search(text)                       # narration is Thai
    assert "outcome=success" in result.note        # matches the committed outcome


async def test_narration_failure_after_commit_does_not_re_execute(db, provider):
    """State is committed BEFORE narration. If narration fails, the engine retries
    narration only (here: safe fallback) — it never re-rolls or re-commits."""
    from tests.support.factories import build_world, start_session_with_scene

    world = await build_world(db)
    await start_session_with_scene(db, world)
    # Force every narration attempt to fail so the narrator must fall back.
    provider.simulate_invalid("generate_dm_narration", 10)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([14]))

    async with db.session() as s:
        hp_before = (await s.get(Character, world.kael_id)).hp

    result = await bridge.handle_inbound(_inbound("n2", "! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))

    # The action still resolved and committed exactly once.
    assert result.state_mutated is True
    checks = await _check_events(db, world.campaign_id)
    assert len(checks) == 1                          # not re-rolled / not duplicated
    assert checks[0].mechanical_changes["natural_roll"] == 14
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).hp == hp_before
    # A fallback narration was still produced.
    assert result.responses and result.responses[0].content


async def test_narration_cannot_change_committed_numbers(db, provider):
    """Even a narrator that returns arbitrary prose cannot move the committed total."""
    from app.schemas.llm_io import Narration
    from tests.support.factories import build_world, start_session_with_scene

    world = await build_world(db)
    await start_session_with_scene(db, world)
    provider.on(
        "generate_dm_narration",
        lambda m, model: Narration(text="เจ้าทอยได้ 30 และสังหารทุกคน!", style="cinematic"),
    )
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([14]))
    await bridge.handle_inbound(_inbound("n3", "! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))

    checks = await _check_events(db, world.campaign_id)
    # The committed record still says 14+5=19, regardless of the narrator's claims.
    assert checks[0].mechanical_changes["total"] == 19
