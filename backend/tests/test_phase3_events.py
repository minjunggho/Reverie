"""Phase 3 acceptance: monotonic event seq, and atomic state+event commit/rollback."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.character import Character
from app.models.enums import EventType, Visibility
from app.models.event import Event
from app.services.events import EventService
from tests.support.factories import build_world, start_session_with_scene


async def test_events_get_monotonic_seq(db):
    world = await build_world(db)
    async with db.unit_of_work() as session:
        svc = EventService(session)
        e1 = await svc.record(campaign_id=world.campaign_id, event_type=EventType.SCENE_STARTED)
        e2 = await svc.record(campaign_id=world.campaign_id, event_type=EventType.PLAYER_ACTION_COMMITTED)
        assert e1.seq == 1
        assert e2.seq == 2


async def test_no_events_created_by_plain_entity_setup(db):
    """Creating campaign/members/characters is NOT itself a canonical event."""
    world = await build_world(db)
    async with db.session() as session:
        count = (
            await session.execute(
                select(func.count(Event.id)).where(Event.campaign_id == world.campaign_id)
            )
        ).scalar_one()
        assert count == 0


async def test_state_and_event_commit_atomically(db):
    world = await build_world(db)
    async with db.unit_of_work() as session:
        kael = await session.get(Character, world.kael_id)
        before = kael.hp
        kael.hp = before - 5
        await EventService(session).record(
            campaign_id=world.campaign_id,
            event_type=EventType.DAMAGE_APPLIED,
            actor_entity=f"character:{world.kael_id}",
            mechanical_changes={"hp": {"from": before, "to": before - 5}},
        )
    # After commit: both the HP change and the event are present.
    async with db.session() as session:
        kael = await session.get(Character, world.kael_id)
        assert kael.hp == 4
        events = await EventService(session).list_events(
            campaign_id=world.campaign_id, event_types=[EventType.DAMAGE_APPLIED]
        )
        assert len(events) == 1
        assert events[0].mechanical_changes["hp"]["to"] == 4


async def test_state_and_event_roll_back_together(db):
    world = await build_world(db)
    async with db.session() as session:
        original_hp = (await session.get(Character, world.kael_id)).hp

    with pytest.raises(RuntimeError):
        async with db.unit_of_work() as session:
            kael = await session.get(Character, world.kael_id)
            kael.hp = 1
            await EventService(session).record(
                campaign_id=world.campaign_id, event_type=EventType.DAMAGE_APPLIED,
            )
            raise RuntimeError("boom before commit")

    # Neither the HP change nor the event survived.
    async with db.session() as session:
        kael = await session.get(Character, world.kael_id)
        assert kael.hp == original_hp
        count = (
            await session.execute(
                select(func.count(Event.id)).where(Event.campaign_id == world.campaign_id)
            )
        ).scalar_one()
        assert count == 0
        # The seq counter also rolled back (no phantom increment).
        from app.models.campaign import Campaign

        seq = (await session.get(Campaign, world.campaign_id)).event_seq
        assert seq == 0


async def test_visibility_filtered_reads(db):
    world = await build_world(db)
    session_id, scene_id = await start_session_with_scene(db, world)
    async with db.unit_of_work() as session:
        svc = EventService(session)
        await svc.record(campaign_id=world.campaign_id, session_id=session_id,
                         event_type=EventType.SCENE_STARTED, visibility=Visibility.PUBLIC)
        await svc.record(campaign_id=world.campaign_id, session_id=session_id,
                         event_type=EventType.NPC_STATE_CHANGED, visibility=Visibility.DM_ONLY,
                         payload={"secret": "ยามคนนี้เป็นสายลับ"})
    async with db.session() as session:
        visible = await EventService(session).list_visible_events(
            campaign_id=world.campaign_id, session_id=session_id,
            allowed_visibilities=[Visibility.PUBLIC, Visibility.PARTY],
        )
        assert len(visible) == 1
        assert visible[0].visibility == Visibility.PUBLIC.value
        # The DM-only secret is not selectable through the player-safe read.
        assert all("secret" not in (e.payload or {}) for e in visible)
