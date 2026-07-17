"""CharacterNarrativeContext — bounded, player-safe, never invented (issue #1)."""
from __future__ import annotations

from app.memory.character_context import build_character_narrative_context
from app.models.character import Character
from app.models.enums import EventType, Visibility
from app.services.events import EventService
from tests.support.factories import build_world


async def _set_hooks(db, character_id, hooks, appearance=""):
    async with db.unit_of_work() as s:
        char = await s.get(Character, character_id)
        char.hooks = hooks
        if appearance:
            char.appearance = appearance


async def test_relevant_hook_surfaces_when_it_shares_a_keyword_with_the_action(db):
    world = await build_world(db)
    await _set_hooks(db, world.kael_id, {"fear": "กลัวความมืดในถ้ำลึก", "desire": "อยากมีเงินพอเลี้ยงน้อง"})

    async with db.session() as read:
        character = await read.get(Character, world.kael_id)
        ctx = await build_character_narrative_context(
            read, character=character, action_text="เดินเข้าไปในถ้ำลึกที่มืดสนิท",
            campaign_id=world.campaign_id,
        )
    assert "fear" in ctx.relevant_hooks
    assert "desire" not in ctx.relevant_hooks   # no keyword overlap -> not surfaced


async def test_irrelevant_hooks_are_not_dumped_into_every_request(db):
    world = await build_world(db)
    await _set_hooks(db, world.kael_id, {
        "origin": "เติบโตในเมืองท่าทางใต้", "fear": "กลัวน้ำลึก", "flaw": "ขี้ระแวงเกินเหตุ",
    })
    async with db.session() as read:
        character = await read.get(Character, world.kael_id)
        ctx = await build_character_narrative_context(
            read, character=character, action_text="เปิดประตูไม้ธรรมดา",
            campaign_id=world.campaign_id,
        )
    assert ctx.relevant_hooks == {}   # nothing about a plain door matches any hook


async def test_saving_throw_always_surfaces_fear_and_flaw_when_present(db):
    """The exact moment established trauma is allowed to matter (test case 4)."""
    world = await build_world(db)
    await _set_hooks(db, world.kael_id, {
        "fear": "กลัวเสียงระฆังที่ดังตอนไฟไหม้บ้านเก่า", "flaw": "แข็งตัวเมื่อได้ยินเสียงระฆัง",
        "desire": "ไม่เกี่ยวข้อง",
    })
    async with db.session() as read:
        character = await read.get(Character, world.kael_id)
        ctx = await build_character_narrative_context(
            read, character=character, action_text="ได้ยินเสียงระฆังดังขึ้นอีกครั้ง",
            is_saving_throw=True, campaign_id=world.campaign_id,
        )
    assert "fear" in ctx.relevant_hooks
    assert "flaw" in ctx.relevant_hooks


async def test_context_never_invents_a_hook_that_does_not_exist(db):
    world = await build_world(db)
    # No hooks set at all — hooks defaults to {}.
    async with db.session() as read:
        character = await read.get(Character, world.kael_id)
        ctx = await build_character_narrative_context(
            read, character=character, action_text="ทอย wisdom saving throw กลัวความมืด",
            is_saving_throw=True, campaign_id=world.campaign_id,
        )
    assert ctx.relevant_hooks == {}
    assert ctx.objective == ""
    assert ctx.relationship == ""
    assert "spouse" not in ctx.as_block()
    assert "ลูก" not in ctx.as_block()


async def test_recent_events_are_bounded_to_three_and_visibility_filtered(db):
    world = await build_world(db)
    kael_ref = f"character:{world.kael_id}"
    async with db.unit_of_work() as s:
        events = EventService(s)
        for i in range(5):
            await events.record(
                campaign_id=world.campaign_id, event_type=EventType.PLAYER_ACTION_COMMITTED,
                actor_entity=kael_ref, visibility=Visibility.PARTY,
                payload={"summary": f"เหตุการณ์ที่มองเห็นได้ #{i}"},
            )
        # A DM-only event mentioning Kael must never surface in player-safe context.
        await events.record(
            campaign_id=world.campaign_id, event_type=EventType.NPC_STATE_CHANGED,
            target_entities=[kael_ref], visibility=Visibility.DM_ONLY,
            payload={"summary": "ความลับที่ DM รู้เกี่ยวกับ Kael"},
        )

    async with db.session() as read:
        character = await read.get(Character, world.kael_id)
        ctx = await build_character_narrative_context(
            read, character=character, action_text="ทำอะไรบางอย่าง",
            campaign_id=world.campaign_id,
        )
    assert len(ctx.recent_events) == 3
    assert all("ความลับที่ DM รู้" not in e for e in ctx.recent_events)
    # Most recent three, in order.
    assert ctx.recent_events == [
        "เหตุการณ์ที่มองเห็นได้ #2", "เหตุการณ์ที่มองเห็นได้ #3", "เหตุการณ์ที่มองเห็นได้ #4",
    ]


async def test_appearance_and_identity_are_always_included(db):
    world = await build_world(db)
    await _set_hooks(db, world.kael_id, {}, appearance="ตัวเล็ก ผมสีเงิน ตาไวมาก")
    async with db.session() as read:
        character = await read.get(Character, world.kael_id)
        ctx = await build_character_narrative_context(
            read, character=character, action_text="เดินเข้าเมือง", campaign_id=world.campaign_id,
        )
    assert ctx.appearance == "ตัวเล็ก ผมสีเงิน ตาไวมาก"
    block = ctx.as_block()
    assert "Kael" in block
    assert "ตัวเล็ก" in block
