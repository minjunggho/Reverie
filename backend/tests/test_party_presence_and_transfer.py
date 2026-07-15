"""Authoritative party presence (Failure 2) + transactional item transfer (Failure 11).

The invariant: two active characters at the same canonical location can always
perceive, target, and hand items to each other. Presence derives from POSITION (the
canonical truth), never from a stale scene-participants list — a missed membership
record must not "invent distance", and a participant tracked elsewhere must not be a
ghost. Ownership changes only through InventoryService.transfer: possession,
co-location, campaign isolation, and exactly-once idempotency are validated, and the
canonical ITEM_TRANSFERRED event commits with the ledger change.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import ValidationError
from app.entities.directory import SceneEntityDirectory
from app.models.character import Character
from app.models.enums import EventType
from app.models.event import Event
from app.models.location import Location
from app.models.scene import Scene
from app.services.campaigns.inventory_service import InventoryService
from tests.support.factories import build_world, start_session_with_scene


async def _place(db, char_id, location_id):
    async with db.unit_of_work() as s:
        (await s.get(Character, char_id)).location_id = location_id


async def _scene(db, scene_id):
    async with db.session() as s:
        return await s.get(Scene, scene_id)


# --- F2: presence = canonical position, both directions ---------------------------

async def test_colocated_teammate_missing_from_participants_is_still_present(db, provider):
    """The participants list missed Bront, but he STANDS at the scene's location —
    the directory must surface him as present and resolvable (never 'invent distance')."""
    world = await build_world(db)
    _, scene_id = await start_session_with_scene(db, world)
    await _place(db, world.kael_id, world.location_id)
    await _place(db, world.bront_id, world.location_id)
    async with db.unit_of_work() as s:              # corrupt: membership record lost
        scene = await s.get(Scene, scene_id)
        scene.participants = [f"character:{world.kael_id}"]

    async with db.session() as s:
        directory = await SceneEntityDirectory(s).build(
            await s.get(Scene, scene_id), actor_character_id=world.kael_id,
            campaign_id=world.campaign_id)
    present = {e.canonical_name for e in directory.present_player_characters}
    assert "Bront" in present                       # healed from position
    resolution = directory.resolve_mentions(["Bront"])
    assert resolution.resolved and not resolution.not_present


async def test_participant_tracked_elsewhere_is_not_a_ghost(db, provider):
    """A listed participant whose canonical position is another room is NOT present."""
    world = await build_world(db)
    _, scene_id = await start_session_with_scene(db, world)
    await _place(db, world.kael_id, world.location_id)
    async with db.unit_of_work() as s:
        room = Location(campaign_id=world.campaign_id, name="ลานหลังบ้าน")
        s.add(room)
        await s.flush()
        (await s.get(Character, world.bront_id)).location_id = room.id

    async with db.session() as s:
        directory = await SceneEntityDirectory(s).build(
            await s.get(Scene, scene_id), actor_character_id=world.kael_id,
            campaign_id=world.campaign_id)
    present = {e.canonical_name for e in directory.present_player_characters}
    assert "Bront" not in present                   # position is the truth
    resolution = directory.resolve_mentions(["Bront"])
    assert resolution.not_present and not resolution.resolved


# --- F11: transactional, exactly-once item transfer --------------------------------

async def _grant_bottle(db, world):
    async with db.unit_of_work() as s:
        await InventoryService(s).grant(
            character_id=world.kael_id, name="ขวดไวน์มีตรา", kind="treasure",
            description="ขวดไวน์เก่า มีตราประทับรูปมงกุฎ", record_event=False)


async def _quantity(db, char_id, name):
    async with db.session() as s:
        rows = await InventoryService(s).list_inventory(char_id)
    return next((e.quantity for e, i in rows if i.name == name), 0)


async def test_transfer_moves_ownership_and_commits_canonical_event(db, provider):
    world = await build_world(db)
    await _place(db, world.kael_id, world.location_id)
    await _place(db, world.bront_id, world.location_id)
    await _grant_bottle(db, world)

    async with db.unit_of_work() as s:
        await InventoryService(s).transfer(
            from_character_id=world.kael_id, to_character_id=world.bront_id,
            name="ขวดไวน์มีตรา", idempotency_key="give-1")

    assert await _quantity(db, world.kael_id, "ขวดไวน์มีตรา") == 0     # sender lost it
    assert await _quantity(db, world.bront_id, "ขวดไวน์มีตรา") == 1    # receiver has it
    async with db.session() as s:
        ev = (await s.execute(select(Event).where(
            Event.event_type == EventType.ITEM_TRANSFERRED.value))).scalar_one()
        assert ev.payload["item"] == "ขวดไวน์มีตรา"
        assert ev.payload["idempotency_key"] == "give-1"


async def test_duplicate_transfer_input_does_not_duplicate_the_item(db, provider):
    """The same Discord message delivered twice hands the bottle over ONCE."""
    world = await build_world(db)
    await _place(db, world.kael_id, world.location_id)
    await _place(db, world.bront_id, world.location_id)
    await _grant_bottle(db, world)

    for _ in range(2):                              # duplicated inbound
        async with db.unit_of_work() as s:
            await InventoryService(s).transfer(
                from_character_id=world.kael_id, to_character_id=world.bront_id,
                name="ขวดไวน์มีตรา", idempotency_key="give-dup")

    assert await _quantity(db, world.bront_id, "ขวดไวน์มีตรา") == 1    # exactly once
    async with db.session() as s:
        count = len((await s.execute(select(Event).where(
            Event.event_type == EventType.ITEM_TRANSFERRED.value))).scalars().all())
    assert count == 1


async def test_transfer_requires_authoritative_co_location(db, provider):
    """Handing an item to a teammate in another room is refused with the real
    in-world reason — narration can never claim an impossible hand-over."""
    world = await build_world(db)
    await _place(db, world.kael_id, world.location_id)
    async with db.unit_of_work() as s:
        room = Location(campaign_id=world.campaign_id, name="หอสมุด")
        s.add(room)
        await s.flush()
        (await s.get(Character, world.bront_id)).location_id = room.id
    await _grant_bottle(db, world)

    with pytest.raises(ValidationError, match="ไม่ได้อยู่ตรงนี้"):
        async with db.unit_of_work() as s:
            await InventoryService(s).transfer(
                from_character_id=world.kael_id, to_character_id=world.bront_id,
                name="ขวดไวน์มีตรา")
    assert await _quantity(db, world.kael_id, "ขวดไวน์มีตรา") == 1     # nothing moved


async def test_transfer_requires_actual_possession(db, provider):
    world = await build_world(db)
    await _place(db, world.kael_id, world.location_id)
    await _place(db, world.bront_id, world.location_id)

    with pytest.raises(ValidationError, match="ไม่มี"):
        async with db.unit_of_work() as s:
            await InventoryService(s).transfer(
                from_character_id=world.kael_id, to_character_id=world.bront_id,
                name="ขวดไวน์มีตรา")


async def test_take_it_back_round_trip(db, provider):
    """Give it, take it back, give it again — the ledger stays exact."""
    world = await build_world(db)
    await _place(db, world.kael_id, world.location_id)
    await _place(db, world.bront_id, world.location_id)
    await _grant_bottle(db, world)

    async with db.unit_of_work() as s:
        await InventoryService(s).transfer(
            from_character_id=world.kael_id, to_character_id=world.bront_id,
            name="ขวดไวน์มีตรา", idempotency_key="g1")
    async with db.unit_of_work() as s:
        await InventoryService(s).transfer(
            from_character_id=world.bront_id, to_character_id=world.kael_id,
            name="ขวดไวน์มีตรา", idempotency_key="g2")

    assert await _quantity(db, world.kael_id, "ขวดไวน์มีตรา") == 1
    assert await _quantity(db, world.bront_id, "ขวดไวน์มีตรา") == 0
