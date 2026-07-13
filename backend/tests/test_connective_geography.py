"""Connective geography — multi-hop navigation + the outside rule (§4–5).

Builds on WorldGraphService/TravelService/WorldExpansionService and the
campaign-validation reachability BFS. The engine finds a named destination anywhere
in the reachable world, routes to it through connective geography (never teleporting
building→building), sums the elapsed time over the whole route, infers the minimum
exterior link when a sparse import omits one, and — when a place is real but simply
unreachable — says so instead of fabricating a destination.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.db.session import Database
from app.engine import build_bridge
from app.models.character import Character
from app.models.enums import SceneMode
from app.models.location import Location
from app.models.scene import Scene
from app.models.world_graph import LocationConnection
from app.presentation import MessageKind
from app.schemas.llm_io import ActionInterpretation
from app.services.scenes import SceneService
from app.services.sessions import SessionService
from app.world.graph_service import WorldGraphService
from app.world.route_service import DestinationClass, RouteService
from tests.support.factories import build_world

_n = {"v": 0}


def _msg(content, author="disc-p1", name="กี้"):
    from app.discord_bridge import InboundMessage

    _n["v"] += 1
    return InboundMessage(discord_message_id=f"cg{_n['v']}", guild_id="guild-1",
                          channel_id="chan-1", author_discord_id=author,
                          author_display_name=name, content=content)


async def _loc(s, campaign_id, name, *, loc_type="LOCATION", parent_id=None):
    loc = Location(campaign_id=campaign_id, name=name, location_type=loc_type,
                   parent_id=parent_id, description_obvious=f"{name} obvious")
    s.add(loc)
    await s.flush()
    return loc


async def _town_geo(s, campaign_id):
    """A tavern and a smithy, both interior LOCATIONs under one exterior DISTRICT,
    with authored exterior links but NO direct tavern↔smithy edge."""
    square = await _loc(s, campaign_id, "จัตุรัสตลาด", loc_type="DISTRICT")
    tavern = await _loc(s, campaign_id, "โรงเตี๊ยมกริฟฟิน", parent_id=square.id)
    smithy = await _loc(s, campaign_id, "ร้านตีเหล็ก", parent_id=square.id)
    cellar = await _loc(s, campaign_id, "ห้องใต้ดิน", parent_id=tavern.id)
    graph = WorldGraphService(s)
    await graph.add_connection(campaign_id=campaign_id, from_location_id=tavern.id,
                               to_location_id=square.id, label="ออกสู่จัตุรัส",
                               direction="outside", travel_minutes=1)
    await graph.add_connection(campaign_id=campaign_id, from_location_id=square.id,
                               to_location_id=smithy.id, label="เข้าร้านตีเหล็ก",
                               direction="", travel_minutes=2)
    await graph.add_connection(campaign_id=campaign_id, from_location_id=tavern.id,
                               to_location_id=cellar.id, label="ลงห้องใต้ดิน",
                               direction="down", travel_minutes=0)
    return square, tavern, smithy, cellar


# --- pathfinding ----------------------------------------------------------------

async def test_find_route_prefers_fewest_minutes_then_hops(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        a = await _loc(s, world.campaign_id, "A")
        b = await _loc(s, world.campaign_id, "B")
        c = await _loc(s, world.campaign_id, "C")
        d = await _loc(s, world.campaign_id, "D")
        graph = WorldGraphService(s)
        # Long way A→B→D = 2 min; direct A→D = 9 min.
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=a.id,
                                   to_location_id=b.id, travel_minutes=1)
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=b.id,
                                   to_location_id=d.id, travel_minutes=1)
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=a.id,
                                   to_location_id=d.id, travel_minutes=9)
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=a.id,
                                   to_location_id=c.id, travel_minutes=5)
        route = await RouteService(s).find_route(
            campaign_id=world.campaign_id, from_location_id=a.id, to_location_id=d.id)
    assert route is not None
    assert route.total_minutes == 2 and len(route.hops) == 2   # fewest minutes wins


async def test_find_route_skips_blocked_edges(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        a = await _loc(s, world.campaign_id, "A")
        b = await _loc(s, world.campaign_id, "B")
        graph = WorldGraphService(s)
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=a.id,
                                   to_location_id=b.id, travel_minutes=1, access_state="locked")
        route = await RouteService(s).find_route(
            campaign_id=world.campaign_id, from_location_id=a.id, to_location_id=b.id)
    assert route is None                                       # the only edge is locked


# --- destination resolution + the outside rule ----------------------------------

async def test_named_destination_routes_through_the_exterior(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        square, tavern, smithy, _ = await _town_geo(s, world.campaign_id)
        res = await RouteService(s).resolve_destination(
            campaign_id=world.campaign_id, from_location_id=tavern.id,
            reference="ไปร้านตีเหล็ก")
        assert res.klass is DestinationClass.EXISTING_ROUTED
        assert res.target is not None and res.target.id == smithy.id
        assert res.route.is_multi_hop and res.route.total_minutes == 3
        assert square.name in res.route.waypoint_names        # passed THROUGH the exterior
        assert await RouteService(s).route_obeys_outside_rule(res.route) is True


async def test_direct_building_to_building_edge_violates_the_outside_rule(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        square, tavern, smithy, _ = await _town_geo(s, world.campaign_id)
        # A bogus authored teleport straight from one interior to another.
        await WorldGraphService(s).add_connection(
            campaign_id=world.campaign_id, from_location_id=tavern.id,
            to_location_id=smithy.id, travel_minutes=0)
        rs = RouteService(s)
        teleport = await rs.find_route(campaign_id=world.campaign_id,
                                       from_location_id=tavern.id, to_location_id=smithy.id)
        assert teleport is not None and not teleport.is_multi_hop
        assert await rs.route_obeys_outside_rule(teleport) is False   # tavern→smithy directly


async def test_resolve_destination_unreachable_when_named_but_no_open_route(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        vault = await _loc(s, world.campaign_id, "ห้องนิรภัย")
        here = await _loc(s, world.campaign_id, "ทางเดิน")
        await WorldGraphService(s).add_connection(
            campaign_id=world.campaign_id, from_location_id=here.id,
            to_location_id=vault.id, travel_minutes=1, access_state="locked")
        res = await RouteService(s).resolve_destination(
            campaign_id=world.campaign_id, from_location_id=here.id, reference="ไปห้องนิรภัย")
    assert res.klass is DestinationClass.UNREACHABLE          # real place, locked away
    assert res.target is not None and res.target.name == "ห้องนิรภัย"
    assert res.route is None


async def test_resolve_destination_ordinary_expandable_when_unnamed(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        _, tavern, _, _ = await _town_geo(s, world.campaign_id)
        res = await RouteService(s).resolve_destination(
            campaign_id=world.campaign_id, from_location_id=tavern.id,
            reference="หาร้านขายยาสักแห่ง")                    # no authored place by that name
    assert res.klass is DestinationClass.ORDINARY_EXPANDABLE and res.target is None


# --- deterministic connector inference (sparse worlds) --------------------------

async def test_infer_exterior_link_creates_persists_and_is_idempotent(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'geo.db').as_posix()}"
    first = Database(url, echo=False)
    await first.create_all()
    try:
        world = await build_world(first)
        async with first.unit_of_work() as s:
            square = await _loc(s, world.campaign_id, "ลานหน้าเมือง", loc_type="DISTRICT")
            hut = await _loc(s, world.campaign_id, "กระท่อมหมอ", parent_id=square.id)
            hut_id, square_id = hut.id, square.id
        async with first.unit_of_work() as s:
            rs = RouteService(s)
            link = await rs.infer_exterior_link(campaign_id=world.campaign_id, location_id=hut_id)
            assert link is not None and link.to_location_id == square_id
            again = await rs.infer_exterior_link(campaign_id=world.campaign_id, location_id=hut_id)
            assert again.id == link.id                          # idempotent, no duplicate
    finally:
        await first.dispose()
    # Survives restart, and the reverse (enter-from-outside) edge is there too.
    restarted = Database(url, echo=False)
    try:
        async with restarted.session() as s:
            conns = list((await s.execute(select(LocationConnection).where(
                LocationConnection.campaign_id == world.campaign_id))).scalars())
            assert any(c.from_location_id == hut_id and c.to_location_id == square_id for c in conns)
            assert any(c.from_location_id == square_id and c.to_location_id == hut_id for c in conns)
    finally:
        await restarted.dispose()


# --- resolve_exit fixes ---------------------------------------------------------

async def test_empty_direction_is_never_outside(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        here = await _loc(s, world.campaign_id, "ห้องโถง")
        blank = await _loc(s, world.campaign_id, "ที่ไหนสักแห่ง")
        pit = await _loc(s, world.campaign_id, "หลุม")
        tagged = await _loc(s, world.campaign_id, "ถนน")
        graph = WorldGraphService(s)
        # Two exits (so the single-exit fallback can't fire); NEITHER is tagged
        # "outside", so "go outside" must not resolve to the empty-direction edge.
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=here.id,
                                   to_location_id=blank.id, label="", direction="")
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=here.id,
                                   to_location_id=pit.id, label="", direction="down")
        m = await graph.resolve_exit(from_location_id=here.id, reference="ออกไปข้างนอก")
        assert m is None
        # Add a properly-tagged exterior edge; now "outside" resolves.
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=here.id,
                                   to_location_id=tagged.id, label="ประตูหน้า", direction="outside")
        m2 = await graph.resolve_exit(from_location_id=here.id, reference="ออกไปข้างนอก")
        assert m2 is not None and m2.connection.to_location_id == tagged.id


# --- pipeline integration -------------------------------------------------------

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


async def test_multi_hop_travel_passes_through_exterior_and_sums_time(db, provider):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        _, tavern, smithy, _ = await _town_geo(s, world.campaign_id)
        tavern_id, smithy_id, smithy_name = tavern.id, smithy.id, smithy.name
    sid = await _session_scene_at(db, world, tavern_id)
    before = await _game_time(db, world.campaign_id)

    provider.on("interpret_committed_action", _movement("ไปร้านตีเหล็ก"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมเดินไปร้านตีเหล็ก"))

    assert r.state_mutated and smithy_name in r.note
    frame = r.responses[0]
    assert frame.kind == MessageKind.SCENE_FRAME and frame.title == smithy_name
    assert "จัตุรัสตลาด" in frame.data.get("footer", "")         # the route is shown, not hidden
    async with db.session() as s:
        kael = await s.get(Character, world.kael_id)
        assert kael.location_id == smithy_id                    # arrived at the far building
        after = (await _game_time(db, world.campaign_id))
    assert after == before + 3                                   # 1 (out) + 2 (in) summed


async def test_unreachable_named_place_is_declined_not_fabricated(db, provider):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        square = await _loc(s, world.campaign_id, "ตรอกมืด", loc_type="DISTRICT")
        here = await _loc(s, world.campaign_id, "ประตูเมือง", parent_id=square.id)
        gate = await _loc(s, world.campaign_id, "ป้อมยาม", parent_id=square.id)
        # A real vault with NO parent, reachable only through a locked gate off the
        # square → inference can't bridge it, so the engine must decline, not invent.
        vault = await _loc(s, world.campaign_id, "ห้องนิรภัยหลวง")
        graph = WorldGraphService(s)
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=here.id,
                                   to_location_id=square.id, direction="outside", travel_minutes=1)
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=here.id,
                                   to_location_id=gate.id, direction="down", travel_minutes=1)
        await graph.add_connection(campaign_id=world.campaign_id, from_location_id=square.id,
                                   to_location_id=vault.id, travel_minutes=1, access_state="locked")
        here_id = here.id
        count_before = len((await s.execute(select(Location).where(
            Location.campaign_id == world.campaign_id))).scalars().all())
    await _session_scene_at(db, world, here_id)

    provider.on("interpret_committed_action", _movement("ไปห้องนิรภัยหลวง"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมเดินไปห้องนิรภัยหลวง"))

    assert not r.state_mutated
    assert r.responses[0].kind == MessageKind.TABLE_NOTICE and "ห้องนิรภัยหลวง" in r.responses[0].content
    async with db.session() as s:
        kael = await s.get(Character, world.kael_id)
        assert kael.location_id == here_id                      # did not move
        count_after = len((await s.execute(select(Location).where(
            Location.campaign_id == world.campaign_id))).scalars().all())
        assert count_after == count_before                      # NOTHING fabricated


async def test_outside_rule_inference_bridges_sibling_buildings(db, provider):
    """Sibling interior buildings under one district with NO authored exterior links:
    travel infers the minimum connectors, routes through the exterior, and persists."""
    world = await build_world(db)
    async with db.unit_of_work() as s:
        square = await _loc(s, world.campaign_id, "จัตุรัสเก่า", loc_type="DISTRICT")
        inn = await _loc(s, world.campaign_id, "โรงแรมเงียบ", parent_id=square.id)
        shop = await _loc(s, world.campaign_id, "ร้านหนังสือ", parent_id=square.id)
        inn_id, shop_id = inn.id, shop.id
    await _session_scene_at(db, world, inn_id)

    provider.on("interpret_committed_action", _movement("ไปร้านหนังสือ"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมเดินไปร้านหนังสือ"))

    assert r.state_mutated and "ร้านหนังสือ" in r.note
    async with db.session() as s:
        kael = await s.get(Character, world.kael_id)
        assert kael.location_id == shop_id
        # The inferred connectors persist (inn→square and square→shop both exist).
        conns = list((await s.execute(select(LocationConnection).where(
            LocationConnection.campaign_id == world.campaign_id))).scalars())
        assert any(c.from_location_id == inn_id and c.to_location_id == square.id for c in conns)
        assert any(c.from_location_id == square.id and c.to_location_id == shop_id for c in conns)
        # Never an inn→shop teleport.
        assert not any(c.from_location_id == inn_id and c.to_location_id == shop_id for c in conns)


async def _game_time(db, campaign_id):
    from app.models.campaign import Campaign

    async with db.session() as s:
        camp = await s.get(Campaign, campaign_id)
        return camp.current_game_time if camp else 0
