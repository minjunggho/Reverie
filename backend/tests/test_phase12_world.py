"""Phase 12 acceptance: advancing time ticks due threats/events via domain services;
the AI narrates only perceivable consequences; not every rest is punished."""
from __future__ import annotations

from sqlalchemy import func, select

from app.models.campaign import Campaign
from app.models.enums import EventType, Visibility
from app.models.event import Event
from app.models.world import ScheduledWorldEvent, Threat
from app.world import ThreatService, WorldClockService
from tests.support.factories import build_world


async def test_advancing_time_ticks_due_threat(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        await ThreatService(s).create_threat(
            campaign_id=world.campaign_id, name="โบสถ์เงินตามล่า", goal="หาตัวปาร์ตี้",
            scheduled_game_time=100, tick_amount=15, tick_interval=240, progress=50,
        )
    # Advance past the threat's scheduled time.
    async with db.unit_of_work() as s:
        result = await WorldClockService(s).advance_time(
            campaign_id=world.campaign_id, minutes=120  # 0 -> 120, passes 100
        )
    assert len(result.ticked_threats) == 1
    async with db.session() as s:
        threat = (await s.execute(select(Threat))).scalar_one()
        assert threat.progress == 65                       # 50 + 15
        assert threat.scheduled_game_time == 120 + 240     # rescheduled
        campaign = await s.get(Campaign, world.campaign_id)
        assert campaign.current_game_time == 120
        # Threat advancement is DM-scoped.
        adv = (
            await s.execute(
                select(Event).where(Event.event_type == EventType.THREAT_ADVANCED.value)
            )
        ).scalar_one()
        assert adv.visibility == Visibility.DM_ONLY.value


async def test_not_due_threat_does_not_tick(db):
    """Advancing a little time (a short rest) does not punish the party."""
    world = await build_world(db)
    async with db.unit_of_work() as s:
        await ThreatService(s).create_threat(
            campaign_id=world.campaign_id, name="ภัยไกล", scheduled_game_time=1000, progress=10,
        )
    async with db.unit_of_work() as s:
        result = await WorldClockService(s).advance_time(campaign_id=world.campaign_id, minutes=30)
    assert result.ticked_threats == []
    async with db.session() as s:
        assert (await s.execute(select(Threat))).scalar_one().progress == 10  # unchanged


async def test_scheduled_event_fires_and_perceivable_flag_controls_visibility(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        ts = ThreatService(s)
        await ts.schedule_event(campaign_id=world.campaign_id, due_game_time=60, kind="storm",
                                payload={"summary": "พายุเข้า"}, perceivable=True)
        await ts.schedule_event(campaign_id=world.campaign_id, due_game_time=60, kind="spy_report",
                                payload={"summary": "สายลับส่งข่าว"}, perceivable=False)
    async with db.unit_of_work() as s:
        result = await WorldClockService(s).advance_time(campaign_id=world.campaign_id, minutes=90)
    assert len(result.fired_events) == 2
    # Only the perceivable one is surfaced for narration.
    assert result.perceivable_notes == ["พายุเข้า"]
    async with db.session() as s:
        remaining = (
            await s.execute(select(func.count(ScheduledWorldEvent.id)).where(
                ScheduledWorldEvent.resolved.is_(False)))
        ).scalar_one()
        assert remaining == 0  # both resolved


async def test_consequence_advance_time_ticks_threats_through_same_path(db, provider):
    """A committed action whose consequence advances time must tick due threats too."""
    from app.core.randomness import SequenceRandomness
    from app.discord_bridge import InboundMessage
    from app.engine import build_bridge
    from app.schemas.llm_io import ConsequenceProposal, ProposedDelta
    from app.models.enums import ConsequenceClass
    from tests.support.factories import start_session_with_scene

    world = await build_world(db)
    await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        await ThreatService(s).create_threat(
            campaign_id=world.campaign_id, name="เวลาบีบ", scheduled_game_time=10,
            tick_amount=20, progress=0,
        )
    provider.on(
        "plan_consequence",
        lambda m, model: ConsequenceProposal(
            consequence_class=ConsequenceClass.SUCCESS_WITH_COST,
            deltas=[ProposedDelta(kind="advance_time", payload={"minutes": 60})],
        ),
    )
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([18]))
    await bridge.handle_inbound(InboundMessage(
        discord_message_id="w1", guild_id="g", channel_id="chan-1",
        author_discord_id="disc-p1", author_display_name="กี้",
        content="! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น",
    ))
    async with db.session() as s:
        threat = (await s.execute(select(Threat))).scalar_one()
        assert threat.progress == 20  # ticked because time advanced past its schedule
