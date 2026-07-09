"""Phase 9 acceptance: session opening produces a player-safe recap and reminders;
the recap is structurally incapable of leaking DM-only info."""
from __future__ import annotations

from sqlalchemy import select

from app.ai.jobs import SafeRecapGenerator
from app.models.campaign import CampaignMember
from app.models.character import Character
from app.models.enums import EventType, SceneMode, SessionStatus, Visibility
from app.models.event import Event
from app.models.session import Session
from app.services.events import EventService
from app.services.sessions import SessionOpeningService
from tests.support.factories import build_world


async def test_open_new_session_creates_scene_and_events(db, provider):
    world = await build_world(db)
    opener = SessionOpeningService(db, provider)
    result = await opener.open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=[world.p1_member_id, world.p2_member_id],
        location_id=world.location_id,
        scene_purpose="หาทางเข้าคฤหาสน์เงียบๆ",
        visible_entity_ids=[f"npc:{world.guard_npc_id}"],
        immediate_threat_ids=[f"npc:{world.guard_npc_id}"],
        mode=SceneMode.EXPLORATION,
    )
    assert result.number == 1
    assert result.opening_text  # framing produced

    async with db.session() as s:
        sess = await s.get(Session, result.session_id)
        assert sess.status == SessionStatus.ACTIVE_PLAY.value
        started = await EventService(s).list_events(
            campaign_id=world.campaign_id, event_types=[EventType.SESSION_STARTED]
        )
        scene_started = await EventService(s).list_events(
            campaign_id=world.campaign_id, event_types=[EventType.SCENE_STARTED]
        )
        assert len(started) == 1 and len(scene_started) == 1


async def test_reminders_surface_only_mechanically_relevant_state(db, provider):
    world = await build_world(db)
    # Injure Kael so a reminder is warranted.
    async with db.unit_of_work() as s:
        kael = await s.get(Character, world.kael_id)
        kael.hp = 3
        kael.conditions = ["prone"]
    opener = SessionOpeningService(db, provider)
    result = await opener.open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=[world.p1_member_id, world.p2_member_id],
        location_id=world.location_id,
    )
    joined = " | ".join(result.reminders)
    assert "Kael" in joined and "HP 3/9" in joined
    assert "prone" in joined
    # Bront is at full HP with no conditions -> no reminder about Bront.
    assert "Bront" not in joined


async def test_recap_excludes_dm_only_information(db, provider):
    world = await build_world(db)
    # Record a PARTY event (player-visible) and a DM_ONLY secret event.
    async with db.unit_of_work() as s:
        events = EventService(s)
        await events.record(
            campaign_id=world.campaign_id, event_type=EventType.PLAYER_ACTION_COMMITTED,
            visibility=Visibility.PARTY, payload={"summary": "พวกเขาเปิดประตูเข้าไปในโถง"},
        )
        await events.record(
            campaign_id=world.campaign_id, event_type=EventType.NPC_STATE_CHANGED,
            visibility=Visibility.DM_ONLY,
            payload={"summary": "SECRET_ยามคนนี้เป็นสายลับของโบสถ์เงิน"},
        )

    async with db.session() as read:
        recap = await SafeRecapGenerator(provider).run(
            read, campaign_id=world.campaign_id, session_id=None
        )
    # The player-visible fact is present; the DM secret is not.
    assert "เปิดประตู" in recap.text
    assert "SECRET_" not in recap.text
    assert "สายลับ" not in recap.text


async def test_player_only_and_dm_only_never_reach_recap_query(db, provider):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        events = EventService(s)
        for vis in (Visibility.DM_ONLY, Visibility.PLAYER_ONLY, Visibility.NPC_SCOPED):
            await events.record(
                campaign_id=world.campaign_id, event_type=EventType.KNOWLEDGE_GAINED,
                visibility=vis, payload={"summary": f"HIDDEN_{vis.value}"},
            )
        await events.record(
            campaign_id=world.campaign_id, event_type=EventType.SCENE_STARTED,
            visibility=Visibility.PUBLIC, payload={"summary": "VISIBLE_ฉากเปิด"},
        )

    async with db.session() as read:
        visible = await EventService(read).list_visible_events(
            campaign_id=world.campaign_id,
            allowed_visibilities=[Visibility.PUBLIC, Visibility.PARTY],
        )
    payloads = " ".join(e.payload.get("summary", "") for e in visible)
    assert "VISIBLE_" in payloads
    assert "HIDDEN_" not in payloads
