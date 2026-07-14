"""Step 7 completion — multilingual/NPC destination resolution, segment-by-segment
travel with mid-route blockage, discovery gating, and committed-graph validation.

Builds directly on the 82143a6 RouteService/TravelService/WorldGraphService work
(reused, not replaced). test_connective_geography.py remains the routing regression
suite; this covers the intelligence + execution completion.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.db.session import Database
from app.engine import build_bridge
from app.models.character import Character
from app.models.enums import SceneMode
from app.models.location import Location
from app.models.npc import NPC
from app.models.world_graph import LocationConnection
from app.presentation import MessageKind
from app.schemas.llm_io import ActionInterpretation
from app.services.scenes import SceneService
from app.services.sessions import SessionService
from app.world.graph_service import WorldGraphService
from app.world.graph_validation import (
    AUTO_REPAIR,
    BLOCKING,
    OWNER_REVIEW,
    safe_auto_repair,
    validate_world_graph,
)
from app.world.location_resolver import LocationResolver
from app.world.route_service import DestinationClass, RouteService
from tests.support.factories import build_world

_n = {"v": 0}


def _msg(content, author="disc-p1", name="กี้"):
    from app.discord_bridge import InboundMessage

    _n["v"] += 1
    return InboundMessage(discord_message_id=f"s7{_n['v']}", guild_id="guild-1",
                          channel_id="chan-1", author_discord_id=author,
                          author_display_name=name, content=content)


async def _loc(s, campaign_id, name, *, loc_type="LOCATION", parent_id=None,
               name_th=None, name_en=None, aliases=None, discovery="KNOWN"):
    loc = Location(campaign_id=campaign_id, name=name, location_type=loc_type,
                   parent_id=parent_id, name_th=name_th, name_en=name_en,
                   aliases=aliases or [], discovery_state=discovery,
                   description_obvious=f"{name} obvious")
    s.add(loc)
    await s.flush()
    return loc


# --- multilingual + alias + NPC resolution (PART 4/5) ---------------------------

async def test_thai_reference_resolves_english_named_location_via_alias(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        cath = await _loc(s, world.campaign_id, "Cathedral District",
                          name_th="เขตมหาวิหาร", aliases=["มหาวิหาร"])
        r = LocationResolver(s)
        # Canonical English, Thai name, and a Thai alias all reach the SAME place.
        m_en = await r.resolve(campaign_id=world.campaign_id, reference="go to Cathedral District")
        m_th = await r.resolve(campaign_id=world.campaign_id, reference="ไปเขตมหาวิหาร")
        m_alias = await r.resolve(campaign_id=world.campaign_id, reference="ไปมหาวิหาร")
    for m in (m_en, m_th, m_alias):
        assert m.resolved and m.match.location.id == cath.id


async def test_mixed_forms_normalize_equivalently(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        shop = await _loc(s, world.campaign_id, "Shield of Faith Emporium",
                          aliases=["shield_of_faith emporium"])
        r = LocationResolver(s)
        a = await r.resolve(campaign_id=world.campaign_id, reference="Shield-of-Faith Emporium")
        b = await r.resolve(campaign_id=world.campaign_id, reference="SHIELD OF FAITH EMPORIUM")
    assert a.resolved and b.resolved and a.match.location.id == shop.id == b.match.location.id


async def test_ambiguous_reference_asks_instead_of_guessing(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        # Two gates that both answer to the shared alias "ประตูเมือง".
        await _loc(s, world.campaign_id, "ประตูเหนือ", aliases=["ประตูเมือง"])
        await _loc(s, world.campaign_id, "ประตูใต้", aliases=["ประตูเมือง"])
        r = await LocationResolver(s).resolve(campaign_id=world.campaign_id, reference="ไปประตูเมือง")
    assert not r.resolved and r.is_ambiguous and len(r.ambiguous) == 2


async def test_npc_directed_goal_routes_to_the_npcs_location(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        gate = await _loc(s, world.campaign_id, "ประตูหมู่บ้าน")
        # A distinct name (the factory already has a "ยามเฝ้าประตู") so the NPC is unique.
        s.add(NPC(campaign_id=world.campaign_id, name="ยามหน้าประตูเมือง",
                  current_location_id=gate.id))
        await s.flush()
        r = await LocationResolver(s).resolve(
            campaign_id=world.campaign_id, reference="ไปหายามหน้าประตูเมือง")
    assert r.resolved and r.match.via == "npc" and r.match.location.id == gate.id


async def test_npc_with_unknown_location_is_flagged_not_teleported(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        s.add(NPC(campaign_id=world.campaign_id, name="พ่อค้าเร่", current_location_id=None))
        await s.flush()
        r = await LocationResolver(s).resolve(
            campaign_id=world.campaign_id, reference="ไปหาพ่อค้าเร่")
    assert not r.resolved and r.npc is not None and r.npc_location_unknown


# --- discovery gating (PART 6) --------------------------------------------------

async def test_hidden_location_is_not_offered_as_a_target(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        lair = await _loc(s, world.campaign_id, "ถ้ำจอมมาร", discovery="HIDDEN")
        r = LocationResolver(s)
        hidden = await r.resolve(campaign_id=world.campaign_id, reference="ไปถ้ำจอมมาร")
        shown = await r.resolve(campaign_id=world.campaign_id, reference="ไปถ้ำจอมมาร",
                                include_hidden=True)
    assert not hidden.resolved                                  # players get no free path
    assert shown.resolved and shown.match.location.id == lair.id   # DM tooling still can


# --- committed-graph validation + safe auto-repair (PART 11) --------------------

async def test_validation_flags_interior_teleport_and_negative_time(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        square = await _loc(s, world.campaign_id, "จัตุรัส", loc_type="DISTRICT")
        tav = await _loc(s, world.campaign_id, "โรงเตี๊ยม", parent_id=square.id)
        shop = await _loc(s, world.campaign_id, "ร้านค้า", parent_id=square.id)
        graph = WorldGraphService(s)
        # A bogus interior→interior teleport, and a negative-time edge.
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=tav.id,
                                   to_location_id=shop.id, travel_minutes=0, one_way=True)
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=square.id,
                                   to_location_id=tav.id, travel_minutes=-5, one_way=True)
        report = await validate_world_graph(s, world.campaign_id)
    kinds = {(i.kind, i.category) for i in report.issues}
    assert ("interior_teleport", OWNER_REVIEW) in kinds
    assert ("negative_travel_time", BLOCKING) in kinds
    assert not report.ok                                        # blocking present


async def test_validation_detects_and_auto_repairs_missing_exit(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        square = await _loc(s, world.campaign_id, "ลานเมือง", loc_type="DISTRICT")
        hut = await _loc(s, world.campaign_id, "กระท่อม", parent_id=square.id)
        # square→hut only (one-way in): hut can be entered but has no way out.
        await WorldGraphService(s).add_connection(
            campaign_id=world.campaign_id, from_location_id=square.id,
            to_location_id=hut.id, travel_minutes=1, one_way=True)
        report = await validate_world_graph(s, world.campaign_id)
        assert any(i.kind == "missing_exit" and i.category == AUTO_REPAIR for i in report.issues)
        repaired = await safe_auto_repair(s, world.campaign_id)
        assert hut.id in repaired
        again = await safe_auto_repair(s, world.campaign_id)   # idempotent
        assert again == []
        after = await validate_world_graph(s, world.campaign_id)
    assert not any(i.kind == "missing_exit" for i in after.issues)


async def test_validation_flags_parent_cycle(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        a = await _loc(s, world.campaign_id, "A", loc_type="DISTRICT")
        b = await _loc(s, world.campaign_id, "B", loc_type="DISTRICT", parent_id=a.id)
        a.parent_id = b.id                                     # A→B→A cycle
        report = await validate_world_graph(s, world.campaign_id)
    assert any(i.kind == "parent_cycle" and i.category == BLOCKING for i in report.issues)


# --- segment-by-segment travel execution (PART 7) -------------------------------

async def _session_scene_at(db, world, location_id):
    async with db.unit_of_work() as s:
        kael = await s.get(Character, world.kael_id)
        kael.location_id = location_id
        sess = await SessionService(s).create_session(
            campaign_id=world.campaign_id, attendance=[world.p1_member_id])
        await SessionService(s).start_session(sess.id)
        await SceneService(s).create_scene(
            session_id=sess.id, location_id=location_id, mode=SceneMode.EXPLORATION,
            participants=[f"character:{world.kael_id}"])
        return sess.id


def _movement(reference, kind="CANONICAL_TRAVEL"):
    return lambda m, model: ActionInterpretation(
        goal=reference, method="เดิน", intent_confidence=0.9,
        movement_intent=True, movement_kind=kind, movement_reference=reference)


async def _game_time(db, campaign_id):
    from app.models.campaign import Campaign

    async with db.session() as s:
        camp = await s.get(Campaign, campaign_id)
        return camp.current_game_time if camp else 0


async def test_blocked_midway_stops_at_last_valid_location_no_teleport(db, provider):
    """A three-hop route whose FINAL segment is blocked: the party advances through
    the completed segments, the elapsed time reflects only those, and it stops at the
    last valid location instead of teleporting to the destination."""
    world = await build_world(db)
    async with db.unit_of_work() as s:
        a = await _loc(s, world.campaign_id, "ต้นทาง")
        b = await _loc(s, world.campaign_id, "กลางทาง")
        c = await _loc(s, world.campaign_id, "ปลายทางไกล")
        graph = WorldGraphService(s)
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=a.id,
                                   to_location_id=b.id, travel_minutes=4, one_way=True)
        # The last leg is blocked from the start — routing wouldn't pick it, so we
        # build the plan explicitly to exercise mid-route re-validation.
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=b.id,
                                   to_location_id=c.id, travel_minutes=9, one_way=True,
                                   access_state="blocked")
        a_id, b_id, c_id = a.id, b.id, c.id
    sid = await _session_scene_at(db, world, a_id)
    before = await _game_time(db, world.campaign_id)

    # Drive the segment executor directly with the full (a→b→c) plan.
    from app.world.travel_service import TravelService

    ts = TravelService(db, provider)

    class _Ctx:
        campaign_id = world.campaign_id
        character_id = world.kael_id
        session_id = sid
    async with db.unit_of_work() as s:
        conns = list((await s.execute(select(LocationConnection).where(
            LocationConnection.campaign_id == world.campaign_id,
            LocationConnection.travel_minutes > 0))).scalars())
        hop_ids = [next(c.id for c in conns if c.from_location_id == a_id and c.to_location_id == b_id),
                   next(c.id for c in conns if c.from_location_id == b_id and c.to_location_id == c_id)]
        walk = await ts._execute_route(s, ctx=_Ctx, hop_ids=hop_ids,
                                       movers=[world.kael_id], origin_id=a_id)
    assert walk.reached_id == b_id                             # stopped at the last valid stop
    assert walk.elapsed_minutes == 4                           # only the completed leg counted
    assert walk.blocked_note                                    # explained the blockage
    async with db.session() as s:
        kael = await s.get(Character, world.kael_id)
        assert kael.location_id == b_id                        # NEVER teleported to c
    assert await _game_time(db, world.campaign_id) == before + 4


async def test_multilingual_pipeline_travel_reaches_aliased_destination(db, provider):
    """End-to-end: a Thai reference reaches an English-named building via its alias,
    routing through the exterior with summed time — the flagged limitation is closed."""
    world = await build_world(db)
    async with db.unit_of_work() as s:
        square = await _loc(s, world.campaign_id, "Market Square", loc_type="DISTRICT",
                            aliases=["ตลาด"])
        tavern = await _loc(s, world.campaign_id, "Grey Wolf Tavern", parent_id=square.id,
                            aliases=["โรงเตี๊ยมหมาป่าเทา"])
        shop = await _loc(s, world.campaign_id, "Henry's Shop", parent_id=square.id,
                          name_th="ร้านของเฮนรี่")
        graph = WorldGraphService(s)
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=tavern.id,
                                   to_location_id=square.id, direction="outside", travel_minutes=1)
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=square.id,
                                   to_location_id=shop.id, travel_minutes=2)
        tavern_id, shop_id, square_name = tavern.id, shop.id, square.name
    await _session_scene_at(db, world, tavern_id)

    provider.on("interpret_committed_action", _movement("ไปร้านของเฮนรี่"))   # Thai name
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมเดินไปร้านของเฮนรี่"))

    assert r.state_mutated and "Henry's Shop" in r.note
    async with db.session() as s:
        kael = await s.get(Character, world.kael_id)
        assert kael.location_id == shop_id                     # reached the English-named place
    frame = r.responses[0]
    assert frame.kind == MessageKind.SCENE_FRAME
    assert square_name in frame.data.get("footer", "")         # passed through the exterior


async def test_ambiguous_pipeline_reference_asks_and_does_not_move(db, provider):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        here = await _loc(s, world.campaign_id, "ลานกลาง", loc_type="DISTRICT")
        n1 = await _loc(s, world.campaign_id, "ประตูเหนือ", parent_id=here.id, aliases=["ประตูเมือง"])
        n2 = await _loc(s, world.campaign_id, "ประตูใต้", parent_id=here.id, aliases=["ประตูเมือง"])
        graph = WorldGraphService(s)
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=here.id,
                                   to_location_id=n1.id, travel_minutes=1)
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=here.id,
                                   to_location_id=n2.id, travel_minutes=1)
        here_id = here.id
    await _session_scene_at(db, world, here_id)

    provider.on("interpret_committed_action", _movement("ไปประตูเมือง"))   # shared alias
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมเดินไปประตู"))

    assert not r.state_mutated
    assert r.responses[0].kind == MessageKind.TABLE_NOTICE      # a focused question
    async with db.session() as s:
        kael = await s.get(Character, world.kael_id)
        assert kael.location_id == here_id                     # did not move on a guess
