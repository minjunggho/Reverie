"""Phase 10 acceptance: post-session pipeline yields a player-safe summary AND a
private continuity report derived from canonical events; session reaches COMPLETE;
prose is never treated as canon."""
from __future__ import annotations

from app.models.enums import EventType, SessionStatus, Visibility
from app.models.session import Session
from app.services.events import EventService
from app.services.sessions import (
    PostSessionService,
    SessionClosingService,
    SessionOpeningService,
)
from tests.support.factories import build_world


async def _open_and_populate(db, provider, world):
    opener = SessionOpeningService(db, provider)
    opening = await opener.open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=[world.p1_member_id],
        location_id=world.location_id,
        scene_purpose="สำรวจโถง",
    )
    sid = opening.session_id
    # A player-visible fact and a DM-only secret in the same session.
    async with db.unit_of_work() as s:
        events = EventService(s)
        await events.record(campaign_id=world.campaign_id, session_id=sid,
                            event_type=EventType.PLAYER_ACTION_COMMITTED,
                            visibility=Visibility.PARTY,
                            payload={"summary": "เปิดหีบเจอเหรียญเก่า"})
        await events.record(campaign_id=world.campaign_id, session_id=sid,
                            event_type=EventType.NPC_STATE_CHANGED,
                            visibility=Visibility.DM_ONLY,
                            payload={"summary": "SECRET_ยามรายงานต่อโบสถ์เงิน"})
    return sid


async def test_post_session_produces_safe_summary_and_private_continuity(db, provider):
    world = await build_world(db)
    sid = await _open_and_populate(db, provider, world)

    await SessionClosingService(db, provider).close_session(
        campaign_id=world.campaign_id, session_id=sid
    )
    artifacts = await PostSessionService(db, provider).run(
        campaign_id=world.campaign_id, session_id=sid
    )

    # Player-safe summary must not contain the DM secret.
    assert "SECRET_" not in artifacts.player_summary
    assert "โบสถ์เงิน" not in artifacts.player_summary

    # The PRIVATE continuity report legitimately contains the DM-only development.
    report = artifacts.continuity_report
    assert report["canonical_event_count"] >= 3  # session_started, scene_started, +2
    assert any("SECRET_" in (d["payload"].get("summary", "")) for d in report["secret_developments"])
    assert len(report["npc_state_changes"]) == 1

    # Session reached COMPLETE.
    async with db.session() as s:
        assert (await s.get(Session, sid)).status == SessionStatus.COMPLETE.value


async def test_continuity_report_is_derived_from_events_not_prose(db, provider):
    """The continuity numbers come from canonical events + state, so they are stable
    regardless of what the LLM narrates."""
    world = await build_world(db)
    sid = await _open_and_populate(db, provider, world)
    await SessionClosingService(db, provider).close_session(
        campaign_id=world.campaign_id, session_id=sid
    )
    # Even if the analyzer returns garbage prose, the structured report is unaffected.
    from app.schemas.llm_io import PostSessionReport

    provider.on(
        "process_post_session_continuity",
        lambda m, model: PostSessionReport(
            player_summary="ไม่เกี่ยวข้องเลย", continuity_report={"fake": True}
        ),
    )
    artifacts = await PostSessionService(db, provider).run(
        campaign_id=world.campaign_id, session_id=sid
    )
    assert "fake" not in artifacts.continuity_report  # engine-built, not LLM-built
    assert artifacts.continuity_report["current_campaign_time"] >= 0
