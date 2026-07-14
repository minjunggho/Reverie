"""Persistent world consequences (§11–13) acceptance.

Player actions leave durable marks: witnessed crimes (perceived vs identified vs
reported kept separate), reputations, factions/threats that advance on their own
timeline, quests, rumors that spread over time, injuries, and severed routes — all
persisted and, for delayed effects, processed exactly once on the world-clock path.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.db.session import Database
from app.models.character import Character
from app.models.consequences import CrimeRecord, Faction, Quest, Rumor
from app.models.enums import EventType, Visibility
from app.models.event import Event
from app.models.npc import NPC
from app.models.world import ScheduledWorldEvent
from app.models.world_graph import LocationConnection
from app.npcs import NPCService
from app.world import ConsequenceService, LocationService, WitnessService, WorldClockService
from app.world.route_service import RouteService
from tests.support.factories import build_world


async def _place(db: Database, character_id: str, location_id: str) -> None:
    async with db.unit_of_work() as s:
        char = await s.get(Character, character_id)
        char.location_id = location_id


# 1 -------------------------------------------------------------------------
async def test_public_assault_creates_witnesses_and_guard_response(db):
    world = await build_world(db)
    await _place(db, world.kael_id, world.location_id)   # attacker present
    await _place(db, world.bront_id, world.location_id)  # victim present

    async with db.unit_of_work() as s:
        post = await LocationService(s).create_location(
            campaign_id=world.campaign_id, name="ป้อมยาม")
        watch = await NPCService(s).create_npc(
            campaign_id=world.campaign_id, name="สายตรวจ", current_location_id=post.id)
        watch_id = watch.id

    async with db.unit_of_work() as s:
        witness = await WitnessService(s).resolve(
            campaign_id=world.campaign_id, location_id=world.location_id,
            actor_ref=f"character:{world.kael_id}", public=True, loud=True, lit=True)
        # A public, loud, lit assault: those present perceive AND can name the actor.
        assert witness.any_perceived and witness.any_identified
        assert f"npc:{world.guard_npc_id}" in witness.perceivers  # the door guard saw it
        assert witness.perpetrator_ref == f"character:{world.kael_id}"

        cs = ConsequenceService(
            s, campaign_id=world.campaign_id, actor_entity=f"character:{world.kael_id}")
        crime = await cs.record_crime(
            crime_type="assault", location_id=world.location_id, game_time=0,
            victim_ref=f"character:{world.bront_id}", witness_resolution=witness,
            source_event_id="evt-assault-1")
        assert crime.perceived and crime.identified
        assert crime.perpetrator_ref == f"character:{world.kael_id}"
        assert crime.witnesses  # non-empty
        # Guards respond in ten minutes.
        await cs.schedule_response(
            kind="guard_response", due_game_time=10,
            payload={"npc_id": watch_id, "to_location_id": world.location_id},
            idempotency_key="assault-1-response")

    async with db.unit_of_work() as s:
        await WorldClockService(s).advance_time(campaign_id=world.campaign_id, minutes=10)

    async with db.session() as s:
        watch = await s.get(NPC, watch_id)
        assert watch.current_location_id == world.location_id  # the watch arrived
        remaining = (await s.execute(select(func.count(ScheduledWorldEvent.id)).where(
            ScheduledWorldEvent.resolved.is_(False)))).scalar_one()
        assert remaining == 0


# 2 -------------------------------------------------------------------------
async def test_hidden_theft_unknown_until_discovered(db):
    world = await build_world(db)
    await _place(db, world.kael_id, world.location_id)

    async with db.unit_of_work() as s:
        # A concealed pickpocket in a non-public, quiet space: the act goes unnoticed.
        witness = await WitnessService(s).resolve(
            campaign_id=world.campaign_id, location_id=world.location_id,
            actor_ref=f"character:{world.kael_id}", public=False, loud=False,
            actor_concealed=True)
        assert not witness.any_perceived

        cs = ConsequenceService(
            s, campaign_id=world.campaign_id, actor_entity=f"character:{world.kael_id}")
        crime = await cs.record_crime(
            crime_type="theft", location_id=world.location_id,
            witness_resolution=witness, source_event_id="evt-theft-1")
        crime_id = crime.id
        assert crime.perceived is False and crime.perpetrator_ref is None

    async with db.session() as s:
        # Unperceived → the crime event is DM-only; players cannot learn of it.
        ev = (await s.execute(select(Event).where(
            Event.event_type == EventType.CRIME_RECORDED.value))).scalar_one()
        assert ev.visibility == Visibility.DM_ONLY.value
        assert (await s.get(CrimeRecord, crime_id)).perceived is False

    async with db.unit_of_work() as s:
        crime = await ConsequenceService(
            s, campaign_id=world.campaign_id).discover_crime(crime_id=crime_id)
        assert crime.perceived is True


# 3 -------------------------------------------------------------------------
async def test_disguise_prevents_identity_attribution(db):
    world = await build_world(db)
    await _place(db, world.kael_id, world.location_id)

    async with db.unit_of_work() as s:
        witness = await WitnessService(s).resolve(
            campaign_id=world.campaign_id, location_id=world.location_id,
            actor_ref=f"character:{world.kael_id}", public=True, loud=True,
            actor_disguised=True)
        assert witness.any_perceived         # the guard sees the attack
        assert not witness.any_identified    # but cannot name the disguised actor
        assert witness.perpetrator_ref is None

        crime = await ConsequenceService(
            s, campaign_id=world.campaign_id,
            actor_entity=f"character:{world.kael_id}").record_crime(
            crime_type="assault", location_id=world.location_id,
            witness_resolution=witness, source_event_id="evt-assault-disg")
        assert crime.perceived is True
        assert crime.identified is False
        assert crime.perpetrator_ref is None  # an open, unattributed crime


# 4 -------------------------------------------------------------------------
async def test_rumor_spreads_over_time(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        cs = ConsequenceService(s, campaign_id=world.campaign_id)
        rumor = await cs.spread_rumor(
            content="มีคนเห็นเงาประหลาดในคฤหาสน์",
            origin_location_id=world.location_id, source_event_id="evt-rumor-1")
        rumor_id = rumor.id
        assert rumor.known_scope == "LOCAL"
        # Idempotent per source event: the same trigger never seeds two rumors.
        again = await cs.spread_rumor(content="x", source_event_id="evt-rumor-1")
        assert again.id == rumor_id
        # The rumor reaches the next district later.
        await cs.schedule_response(
            kind="rumor_spread", due_game_time=240, payload={"rumor_id": rumor_id},
            idempotency_key="rumor-1-spread")

    async with db.unit_of_work() as s:
        await WorldClockService(s).advance_time(campaign_id=world.campaign_id, minutes=300)

    async with db.session() as s:
        rumor = await s.get(Rumor, rumor_id)
        assert rumor.known_scope == "SETTLEMENT"  # climbed one rung
        assert rumor.spread_stage == 1


# 5 -------------------------------------------------------------------------
async def test_faction_responds_later(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        cs = ConsequenceService(s, campaign_id=world.campaign_id)
        faction = await cs.create_faction(
            name="สมาคมเงา", goal="ยึดครองท่าเรือ", progress=20, disposition_to_party=0)
        fid = faction.id
        # The faction sends agents after two days (2880 in-world minutes).
        await cs.schedule_response(
            kind="faction_action", due_game_time=2880,
            payload={"faction_id": fid, "progress_delta": 15, "disposition_delta": -30},
            idempotency_key="faction-agents")

    async with db.unit_of_work() as s:
        await WorldClockService(s).advance_time(campaign_id=world.campaign_id, minutes=60)
    async with db.session() as s:
        f = await s.get(Faction, fid)
        assert f.progress == 20 and f.disposition_to_party == 0  # not due yet

    async with db.unit_of_work() as s:
        await WorldClockService(s).advance_time(campaign_id=world.campaign_id, minutes=3000)
    async with db.session() as s:
        f = await s.get(Faction, fid)
        assert f.progress == 35              # advanced on its own timeline
        assert f.disposition_to_party == -30  # now hostile to the party


# 6 -------------------------------------------------------------------------
async def test_destroyed_bridge_changes_navigation_after_restart(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'bridge.sqlite3').as_posix()}"
    first = Database(url)
    await first.create_all()
    try:
        world = await build_world(first)
        async with first.unit_of_work() as s:
            far = await LocationService(s).create_location(
                campaign_id=world.campaign_id, name="ฝั่งตรงข้ามสะพาน")
            far_id = far.id
            for a, b in ((world.location_id, far_id), (far_id, world.location_id)):
                s.add(LocationConnection(
                    campaign_id=world.campaign_id, from_location_id=a, to_location_id=b,
                    travel_minutes=10, obvious=True, access_state="open"))
        async with first.session() as s:
            route = await RouteService(s).find_route(
                campaign_id=world.campaign_id,
                from_location_id=world.location_id, to_location_id=far_id)
            assert route is not None  # the bridge is passable
        async with first.unit_of_work() as s:
            await ConsequenceService(s, campaign_id=world.campaign_id).change_access_state(
                from_location_id=world.location_id, to_location_id=far_id, state="blocked",
                reason="สะพานถูกทำลาย")
    finally:
        await first.dispose()

    restarted = Database(url)
    try:
        async with restarted.session() as s:
            route = await RouteService(s).find_route(
                campaign_id=world.campaign_id,
                from_location_id=world.location_id, to_location_id=far_id)
            assert route is None  # navigation stays severed after restart
            edge = (await s.execute(select(LocationConnection).where(
                LocationConnection.from_location_id == world.location_id,
                LocationConnection.to_location_id == far_id))).scalar_one()
            assert edge.access_state == "blocked"
    finally:
        await restarted.dispose()


# 7 -------------------------------------------------------------------------
async def test_npc_injury_persists_across_restart(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'injury.sqlite3').as_posix()}"
    first = Database(url)
    await first.create_all()
    try:
        world = await build_world(first)
        async with first.unit_of_work() as s:
            await ConsequenceService(s, campaign_id=world.campaign_id).injure_npc(
                npc_id=world.guard_npc_id, severity="gravely_wounded", reason="ถูกฟันเข้าที่ไหล่")
    finally:
        await first.dispose()

    restarted = Database(url)
    try:
        async with restarted.session() as s:
            npc = await s.get(NPC, world.guard_npc_id)
            assert npc.physical_state == "gravely_wounded"
            assert npc.available is False  # an injured NPC who's out of action stays so
    finally:
        await restarted.dispose()


# 8 -------------------------------------------------------------------------
async def test_quest_changes_persist_across_restart(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'quest.sqlite3').as_posix()}"
    first = Database(url)
    await first.create_all()
    try:
        world = await build_world(first)
        campaign_id = world.campaign_id
        async with first.unit_of_work() as s:
            await ConsequenceService(s, campaign_id=campaign_id).update_quest(
                key="find_heir", name="ตามหาทายาท", state="ACTIVE", progress=40,
                data={"lead": "โรงเตี๊ยม"})
    finally:
        await first.dispose()

    restarted = Database(url)
    try:
        async with restarted.session() as s:
            q = (await s.execute(select(Quest).where(Quest.key == "find_heir"))).scalar_one()
            assert q.state == "ACTIVE" and q.progress == 40
            assert q.data.get("lead") == "โรงเตี๊ยม"
        # A later update upserts the same quest, not a duplicate.
        async with restarted.unit_of_work() as s:
            await ConsequenceService(s, campaign_id=campaign_id).update_quest(
                key="find_heir", state="COMPLETED", progress=100)
        async with restarted.session() as s:
            rows = (await s.execute(select(Quest).where(Quest.key == "find_heir"))).scalars().all()
            assert len(rows) == 1
            assert rows[0].state == "COMPLETED" and rows[0].progress == 100
    finally:
        await restarted.dispose()


# 9 -------------------------------------------------------------------------
async def test_scheduled_event_triggers_exactly_once(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        cs = ConsequenceService(s, campaign_id=world.campaign_id)
        faction = await cs.create_faction(name="กลุ่มลับ", progress=0)
        fid = faction.id
        # Same idempotency key twice → a single scheduled row.
        await cs.schedule_response(
            kind="faction_action", due_game_time=100,
            payload={"faction_id": fid, "progress_delta": 10}, idempotency_key="once")
        await cs.schedule_response(
            kind="faction_action", due_game_time=100,
            payload={"faction_id": fid, "progress_delta": 10}, idempotency_key="once")

    async with db.session() as s:
        count = (await s.execute(select(func.count(ScheduledWorldEvent.id)).where(
            ScheduledWorldEvent.campaign_id == world.campaign_id,
            ScheduledWorldEvent.kind == "faction_action"))).scalar_one()
        assert count == 1  # deduped

    # Advance past the due time twice; the effect must apply exactly once.
    async with db.unit_of_work() as s:
        await WorldClockService(s).advance_time(campaign_id=world.campaign_id, minutes=150)
    async with db.unit_of_work() as s:
        await WorldClockService(s).advance_time(campaign_id=world.campaign_id, minutes=150)

    async with db.session() as s:
        assert (await s.get(Faction, fid)).progress == 10  # +10 once, not +20
        resolved = (await s.execute(select(func.count(ScheduledWorldEvent.id)).where(
            ScheduledWorldEvent.resolved.is_(True)))).scalar_one()
        assert resolved == 1
