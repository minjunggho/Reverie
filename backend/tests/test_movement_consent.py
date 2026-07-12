"""§18 movement consent — co-location is NOT consent.

Only the acting character moves by default. Another player character moves only
when it has an explicit, persistent follow state (or an involuntary effect moves
it). A split party stays split across scenes and sessions, and one player can never
silently drag another's character along merely because both are in the same room.

These drive the REAL TravelService against a two-location world graph.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.discord_bridge.dto import InboundMessage
from app.models.character import Character
from app.models.enums import SceneMode
from app.orchestration.context import ResolvedContext
from app.world import LocationService
from app.world.graph_service import WorldGraphService
from app.world.position_service import PositionService
from app.world.travel_service import TravelService
from tests.support.factories import build_world


def _ctx(world, *, character_id, session_id):
    inbound = InboundMessage(
        discord_message_id="mv1", guild_id="guild-1", channel_id=world.channel_id,
        author_discord_id="disc-p1", author_display_name="กี้", content="! เดินออกไป",
    )
    return ResolvedContext(
        inbound=inbound, campaign_id=world.campaign_id, member_id=world.p1_member_id,
        session_id=session_id, character_id=character_id,
    )


async def _two_room_session(db):
    """A world with hall -> yard (5 min) connected, both PCs placed in the hall,
    an active scene there. Returns (world, session_id, hall_id, yard_id)."""
    from app.models.enums import SceneMode as _SM
    from app.services.scenes import SceneService
    from app.services.sessions import SessionService

    world = await build_world(db)
    async with db.unit_of_work() as s:
        locs = LocationService(s)
        hall = await locs.get_location(world.location_id)
        yard = await locs.create_location(
            campaign_id=world.campaign_id, name="ลานหน้า",
            description_obvious="ลานหินโล่งหน้าอาคาร")
        await WorldGraphService(s).add_connection(
            campaign_id=world.campaign_id, from_location_id=hall.id,
            to_location_id=yard.id, label="ออกไปข้างนอก", travel_minutes=5)
        kael = await s.get(Character, world.kael_id)
        bront = await s.get(Character, world.bront_id)
        kael.location_id = hall.id
        bront.location_id = hall.id
        sess = await SessionService(s).create_session(
            campaign_id=world.campaign_id, attendance=[world.p1_member_id, world.p2_member_id])
        await SessionService(s).start_session(sess.id)
        await SceneService(s).create_scene(
            session_id=sess.id, location_id=hall.id, mode=_SM.EXPLORATION,
            participants=[f"character:{world.kael_id}", f"character:{world.bront_id}"])
        return world, sess.id, hall.id, yard.id


async def test_one_leaves_the_other_stays(db, provider):
    """Two characters in the same room; one leaves; the other remains. Co-location
    is not consent."""
    world, session_id, hall_id, yard_id = await _two_room_session(db)
    travel = TravelService(db, provider)
    r = await travel.travel(_ctx(world, character_id=world.kael_id, session_id=session_id),
                            reference="ออกไปข้างนอก", allow_expansion=False)
    assert r.state_mutated
    async with db.session() as s:
        kael = await s.get(Character, world.kael_id)
        bront = await s.get(Character, world.bront_id)
        assert kael.location_id == yard_id     # the actor moved
        assert bront.location_id == hall_id     # the other stayed — NOT dragged along


async def test_explicit_follower_moves_with_the_leader(db, provider):
    """A character that explicitly agreed to follow travels with the leader."""
    world, session_id, hall_id, yard_id = await _two_room_session(db)
    async with db.unit_of_work() as s:
        await PositionService(s).set_follow(follower_id=world.bront_id, leader_id=world.kael_id)
    travel = TravelService(db, provider)
    await travel.travel(_ctx(world, character_id=world.kael_id, session_id=session_id),
                        reference="ออกไปข้างนอก", allow_expansion=False)
    async with db.session() as s:
        kael = await s.get(Character, world.kael_id)
        bront = await s.get(Character, world.bront_id)
        assert kael.location_id == yard_id
        assert bront.location_id == yard_id     # consenting follower came along


async def test_party_travel_moves_only_consenting_members(db, provider):
    """Three-way: leader + one consenting follower move; a non-consenting member
    stays. (Party travel = the set of members who opted in via follow.)"""
    world, session_id, hall_id, yard_id = await _two_room_session(db)
    # Add a third character in the hall who does NOT consent.
    async with db.unit_of_work() as s:
        from app.services.campaigns import CharacterService
        cara = await CharacterService(s).create_character(
            member_id=world.p2_member_id, name="Cara", char_class="bard",
            abilities={"cha": 14}, proficiencies=[], set_active=False)
        cara.location_id = hall_id
        cara_id = cara.id
        await PositionService(s).set_follow(follower_id=world.bront_id, leader_id=world.kael_id)
    travel = TravelService(db, provider)
    await travel.travel(_ctx(world, character_id=world.kael_id, session_id=session_id),
                        reference="ออกไปข้างนอก", allow_expansion=False)
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).location_id == yard_id   # leader
        assert (await s.get(Character, world.bront_id)).location_id == yard_id  # consented
        assert (await s.get(Character, cara_id)).location_id == hall_id         # did not


async def test_a_follower_who_wandered_off_is_not_dragged(db, provider):
    """Follow state alone isn't enough — a follower must also be co-located. Someone
    who walked away earlier is not teleported to the leader's destination."""
    world, session_id, hall_id, yard_id = await _two_room_session(db)
    async with db.unit_of_work() as s:
        # Bront consented, but is currently somewhere else entirely.
        elsewhere = await LocationService(s).create_location(
            campaign_id=world.campaign_id, name="ห้องใต้ดิน", description_obvious="มืด")
        bront = await s.get(Character, world.bront_id)
        bront.location_id = elsewhere.id
        elsewhere_id = elsewhere.id
        await PositionService(s).set_follow(follower_id=world.bront_id, leader_id=world.kael_id)
    travel = TravelService(db, provider)
    await travel.travel(_ctx(world, character_id=world.kael_id, session_id=session_id),
                        reference="ออกไปข้างนอก", allow_expansion=False)
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).location_id == yard_id
        assert (await s.get(Character, world.bront_id)).location_id == elsewhere_id  # stayed


async def test_split_party_persists_across_a_second_travel(db, provider):
    """After a split, positions stay independent: the character left behind is still
    behind after the actor travels again."""
    world, session_id, hall_id, yard_id = await _two_room_session(db)
    travel = TravelService(db, provider)
    # Kael leaves; Bront stays in the hall.
    await travel.travel(_ctx(world, character_id=world.kael_id, session_id=session_id),
                        reference="ออกไปข้างนอก", allow_expansion=False)
    # Kael goes back; Bront is STILL in the hall the whole time, independently.
    await travel.travel(_ctx(world, character_id=world.kael_id, session_id=session_id),
                        reference="กลับ", allow_expansion=False)
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).location_id == hall_id
        assert (await s.get(Character, world.bront_id)).location_id == hall_id
        # Bront never consented, never moved — the split held throughout.


async def test_concurrent_travel_does_not_overwrite_locations(db, provider):
    """Two characters travel at the same time; each lands at its own destination —
    neither overwrites the other's canonical position."""
    world, session_id, hall_id, yard_id = await _two_room_session(db)
    # A separate destination reachable from the hall for Bront.
    async with db.unit_of_work() as s:
        cellar = await LocationService(s).create_location(
            campaign_id=world.campaign_id, name="ห้องเก็บของ", description_obvious="อับ")
        await WorldGraphService(s).add_connection(
            campaign_id=world.campaign_id, from_location_id=hall_id,
            to_location_id=cellar.id, label="ลงห้องเก็บของ", travel_minutes=2)
        cellar_id = cellar.id

    travel = TravelService(db, provider)
    ctx_k = _ctx(world, character_id=world.kael_id, session_id=session_id)
    ctx_b = ResolvedContext(
        inbound=ctx_k.inbound, campaign_id=world.campaign_id, member_id=world.p2_member_id,
        session_id=session_id, character_id=world.bront_id)
    await asyncio.gather(
        travel.travel(ctx_k, reference="ออกไปข้างนอก", allow_expansion=False),
        travel.travel(ctx_b, reference="ลงห้องเก็บของ", allow_expansion=False),
    )
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).location_id == yard_id
        assert (await s.get(Character, world.bront_id)).location_id == cellar_id


async def test_actor_moving_alone_clears_its_stale_follow_state(db, provider):
    """If a character was following someone but then travels on its own initiative,
    it stops following (it is leading now, not tagging along)."""
    world, session_id, hall_id, yard_id = await _two_room_session(db)
    async with db.unit_of_work() as s:
        await PositionService(s).set_follow(follower_id=world.kael_id, leader_id=world.bront_id)
    travel = TravelService(db, provider)
    await travel.travel(_ctx(world, character_id=world.kael_id, session_id=session_id),
                        reference="ออกไปข้างนอก", allow_expansion=False)
    async with db.session() as s:
        kael = await s.get(Character, world.kael_id)
        assert kael.location_id == yard_id
        assert kael.following_character_id is None      # broke off on its own move
