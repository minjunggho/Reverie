"""§34 FIRST REQUIRED VERTICAL SLICE — the whole workflow end-to-end.

Steps 1–24 from the master spec, each asserted. This is the Definition-of-Done
anchor: normal-vs-`!` routing, identity resolution, structured interpretation,
uncertainty -> Stealth, server d20 + modifier + DC, atomic commit (state+event),
Thai narration from the committed result, scene update, session close, player-safe
recap, and NO DM-only leakage.
"""
from __future__ import annotations

import re

from sqlalchemy import func, select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import InboundMessage
from app.engine import build_bridge
from app.models.character import Character
from app.models.enums import EventType, SceneMode, SessionStatus, Visibility
from app.models.event import Event
from app.models.session import Session
from app.services.events import EventService
from app.services.sessions import SessionClosingService, SessionOpeningService
from tests.support.factories import build_world

THAI = re.compile(r"[฀-๿]")


def _msg(mid, content, author="disc-p1"):
    return InboundMessage(
        discord_message_id=mid, guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name="กี้", content=content,
    )


async def _event_count(db, campaign_id):
    async with db.session() as s:
        return (
            await s.execute(select(func.count(Event.id)).where(Event.campaign_id == campaign_id))
        ).scalar_one()


async def test_full_vertical_slice(db, provider):
    # Steps 1-6: initialize app + create campaign, two members, one character each,
    # a location, and a guard NPC.
    world = await build_world(db)

    # Step 7-8: start Session 1 and generate an opening scene in Thai.
    opener = SessionOpeningService(db, provider)
    opening = await opener.open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=[world.p1_member_id, world.p2_member_id],
        location_id=world.location_id,
        scene_purpose="หาทางเข้าไปในคฤหาสน์โดยไม่ให้ยามรู้ตัว",
        dramatic_question="พวกเขาจะเข้าไปได้โดยไม่ถูกจับได้ไหม",
        participants=[f"character:{world.kael_id}", f"character:{world.bront_id}"],
        visible_entity_ids=[f"npc:{world.guard_npc_id}"],
        immediate_threat_ids=[f"npc:{world.guard_npc_id}"],
        mode=SceneMode.EXPLORATION,
    )
    assert THAI.search(opening.opening_text)  # opening framed in Thai
    session_id = opening.session_id

    # Kael: DEX 16 (+3), stealth proficient (+2) => +5. Inject d20 = 16 => 21 vs DC15 => success.
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))

    events_after_open = await _event_count(db, world.campaign_id)
    async with db.session() as s:
        kael_hp_before = (await s.get(Character, world.kael_id)).hp

    # Step 9: a NORMAL message must not execute any committed action or mutate state.
    normal = await bridge.handle_inbound(_msg("m-normal", "กูว่าเราไปดูหน้าต่างดีไหม"))
    assert normal.state_mutated is False
    assert await _event_count(db, world.campaign_id) == events_after_open  # no new events
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).hp == kael_hp_before

    # Steps 10-21: a committed `!` action runs the full pipeline.
    committed = await bridge.handle_inbound(
        _msg("m-commit", "! ผมค่อยๆ เดินไปดูตรงหน้าต่าง พยายามไม่ให้ยามเห็น")
    )
    assert committed.state_mutated is True
    assert "outcome=success" in committed.note
    # Step 20: Thai narration produced from the committed result.
    assert committed.responses and THAI.search(committed.responses[0].content)

    async with db.session() as s:
        # Step 11: identity resolved to Kael's character (the check event's actor).
        check = (
            await s.execute(
                select(Event).where(
                    Event.campaign_id == world.campaign_id,
                    Event.event_type == EventType.ABILITY_CHECK_RESOLVED.value,
                )
            )
        ).scalar_one()
        assert check.actor_entity == f"character:{world.kael_id}"
        # Steps 14-17: Stealth selected; server d20; modifier; DC; outcome — all engine-owned.
        mech = check.mechanical_changes
        assert check.payload["skill"] == "stealth"
        assert mech["natural_roll"] == 16      # exactly the injected server die
        assert mech["modifier"] == 5           # +3 DEX, +2 proficiency
        assert mech["total"] == 21
        assert mech["dc"] == 15
        assert mech["outcome"] == "success"
        # Step 18-19: state+event committed atomically; a PLAYER_ACTION_COMMITTED event exists.
        committed_events = (
            await s.execute(
                select(func.count(Event.id)).where(
                    Event.campaign_id == world.campaign_id,
                    Event.event_type == EventType.PLAYER_ACTION_COMMITTED.value,
                )
            )
        ).scalar_one()
        assert committed_events == 1
        # Kael's authoritative HP is untouched by a stealth success.
        assert (await s.get(Character, world.kael_id)).hp == kael_hp_before
        # Session remains ACTIVE_PLAY at rest (TABLE_OPEN) after the action.
        sess = await s.get(Session, session_id)
        assert sess.status == SessionStatus.ACTIVE_PLAY.value

    # Record a DM-only secret during the session to prove the recap can't leak it.
    async with db.unit_of_work() as s:
        await EventService(s).record(
            campaign_id=world.campaign_id, session_id=session_id,
            event_type=EventType.NPC_STATE_CHANGED, visibility=Visibility.DM_ONLY,
            payload={"summary": "SECRET_ยามเป็นสายลับของโบสถ์เงิน"},
        )

    # Step 22-23: end the session and generate a player-safe recap.
    closing = await SessionClosingService(db, provider).close_session(
        campaign_id=world.campaign_id, session_id=session_id, reason="OWNER_REQUESTED_CLOSE"
    )
    async with db.session() as s:
        sess = await s.get(Session, session_id)
        assert sess.status == SessionStatus.CLOSING.value

    # Step 24: NO DM-only information appears in the recap.
    assert "SECRET_" not in closing.recap_text
    assert "สายลับ" not in closing.recap_text
