"""Following a teammate (Critical Failure 3): the target IS the destination.

Pins the contract: naming a present teammate never triggers "which direction?"; the
follow relationship is persistent structural state (`following_character_id`); a
follower whose leader already moved on catches up transactionally; a pronoun follow
with exactly one other teammate resolves to them; only a genuinely unknown position
earns an (in-world) clarification.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.engine import build_bridge
from app.discord_bridge import InboundMessage
from app.models.character import Character
from app.models.location import Location
from app.world.graph_service import WorldGraphService
from app.world.travel_service import TravelService
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p2", name="โบ"):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"fl-{_n['v']}", guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name=name, content=content)


def _ctx(world, session_id, character_id):
    return SimpleNamespace(
        campaign_id=world.campaign_id, session_id=session_id,
        character_id=character_id, channel_id="chan-1", processed_message_id=None)


async def _place_party(db, world):
    """Seat both PCs at the opening room (the factory scene doesn't set positions)."""
    async with db.unit_of_work() as s:
        for cid in (world.kael_id, world.bront_id):
            (await s.get(Character, cid)).location_id = world.location_id


async def _following(db, char_id):
    async with db.session() as s:
        return (await s.get(Character, char_id)).following_character_id


async def _location(db, char_id):
    async with db.session() as s:
        return (await s.get(Character, char_id)).location_id


async def _side_room(db, world, *, minutes=0):
    """A second location connected to the opening room, both directions open."""
    async with db.unit_of_work() as s:
        room = Location(campaign_id=world.campaign_id, name="ห้องสมุดเก่า")
        s.add(room)
        await s.flush()
        await WorldGraphService(s).add_connection(
            campaign_id=world.campaign_id, from_location_id=world.location_id,
            to_location_id=room.id, label="ประตูไม้", travel_minutes=minutes,
            obvious=True)
        return room.id


async def test_follow_colocated_teammate_asks_no_direction(db, provider):
    world = await build_world(db)
    session_id, _ = await start_session_with_scene(db, world)
    await _place_party(db, world)
    travel = TravelService(db, provider)

    result = await travel.travel(
        _ctx(world, session_id, world.bront_id), reference="ตาม Kael ไป")

    body = result.responses[0].content
    assert "ทางไหน" not in body and "ทิศทาง" not in body     # never "which direction?"
    assert "Kael" in body
    assert await _following(db, world.bront_id) == world.kael_id   # persistent state
    assert await _location(db, world.bront_id) == world.location_id  # nobody moved


async def test_follow_teammate_who_left_catches_up(db, provider):
    """The leader already walked out; the follower routes to them transactionally."""
    world = await build_world(db)
    session_id, _ = await start_session_with_scene(db, world)
    await _place_party(db, world)
    room_id = await _side_room(db, world)
    async with db.unit_of_work() as s:
        (await s.get(Character, world.kael_id)).location_id = room_id

    result = await TravelService(db, provider).travel(
        _ctx(world, session_id, world.bront_id), reference="ตาม Kael ไป")

    assert await _location(db, world.bront_id) == room_id      # caught up
    assert await _following(db, world.bront_id) == world.kael_id
    blob = "\n".join(m.content for m in result.responses)
    assert "ทางไหน" not in blob                                # no direction question


async def test_go_to_teammate_without_follow_verb_sets_no_follow(db, provider):
    world = await build_world(db)
    session_id, _ = await start_session_with_scene(db, world)
    await _place_party(db, world)

    result = await TravelService(db, provider).travel(
        _ctx(world, session_id, world.bront_id), reference="Kael")

    assert "อยู่ตรงนี้" in result.responses[0].content         # resolved, not questioned
    assert await _following(db, world.bront_id) is None        # no accidental consent


async def test_follow_unknown_position_gets_in_world_reason(db, provider):
    world = await build_world(db)
    session_id, _ = await start_session_with_scene(db, world)
    await _place_party(db, world)
    async with db.unit_of_work() as s:
        (await s.get(Character, world.kael_id)).location_id = None

    result = await TravelService(db, provider).travel(
        _ctx(world, session_id, world.bront_id), reference="ตาม Kael ไป")

    body = result.responses[0].content
    assert "ไม่มีใครรู้" in body                                # in-world reason
    assert "ทางไหน" not in body


async def test_pronoun_follow_resolves_single_teammate(db, provider):
    """'ฉันตามเธอไป' with exactly one other teammate present — through the REAL
    committed pipeline — follows that teammate without asking who."""
    world = await build_world(db)
    await start_session_with_scene(db, world)
    await _place_party(db, world)
    bridge = build_bridge(db, provider=provider)

    result = await bridge.handle_inbound(_msg("! ฉันตามเธอไป"))

    assert await _following(db, world.bront_id) == world.kael_id
    blob = "\n".join(m.content for m in result.responses)
    assert "จะตามใคร" not in blob and "ทางไหน" not in blob


async def test_leader_moves_and_follower_travels_along(db, provider):
    """End-to-end: B follows A; A walks to the side room; B arrives in the same
    transaction (the existing consent walk, proven against the new entry path)."""
    world = await build_world(db)
    session_id, _ = await start_session_with_scene(db, world)
    await _place_party(db, world)
    room_id = await _side_room(db, world)
    travel = TravelService(db, provider)

    await travel.travel(_ctx(world, session_id, world.bront_id), reference="ตาม Kael ไป")
    await travel.travel(_ctx(world, session_id, world.kael_id), reference="ประตูไม้")

    assert await _location(db, world.kael_id) == room_id
    assert await _location(db, world.bront_id) == room_id      # moved together
