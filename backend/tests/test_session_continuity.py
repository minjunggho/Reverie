"""Session continuity — the campaign beginning may initialize only a genuinely new
session (Critical Failure 1).

Pins the anti-reset invariants: an ONGOING campaign whose continuity state (anchor +
character positions) is broken raises a StateIntegrityError instead of silently
falling back to the campaign start; opening a session never teleports a character who
has a live position; a late joiner is placed at the party's current anchor, never at
the campaign's opening location; the cinematic opening is governed by a persisted
played-once flag, not by session numbering.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import StateIntegrityError
from app.discord_bridge import AdminBridge, InboundMessage
from app.engine import build_bridge
from app.models.campaign import Campaign
from app.models.character import Character
from app.models.enums import SessionStatus
from app.models.location import Location
from app.models.session import Session
from app.presentation import MessageKind
from app.services.sessions import SessionOpeningService
from tests.support.factories import build_world

_n = {"v": 0}


def _admin_msg(content, author="owner-1"):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"cont-{_n['v']}", guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name="DM", content=content)


async def _open_session(db, provider, world, location_id="", attendance=None):
    return await SessionOpeningService(db, provider).open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=attendance or [world.p1_member_id, world.p2_member_id],
        location_id=location_id, channel_id="chan-1")


async def _complete(db, session_id):
    async with db.unit_of_work() as s:
        row = await s.get(Session, session_id)
        row.status = SessionStatus.COMPLETE.value


async def _char(db, char_id) -> Character:
    async with db.session() as s:
        return await s.get(Character, char_id)


async def test_broken_continuity_raises_integrity_error_instead_of_reset(db, provider):
    """Ongoing campaign + dangling anchor + no character positions → a loud,
    recoverable integrity error. NOT a silent teleport to the campaign start."""
    world = await build_world(db)
    opening = await _open_session(db, provider, world, location_id=world.location_id)
    await _complete(db, opening.session_id)

    # Corrupt continuity the way a bad re-import does: anchor dangles, positions gone.
    async with db.unit_of_work() as s:
        campaign = await s.get(Campaign, world.campaign_id)
        campaign.current_party_anchor_id = "0" * 32          # dangling row id
        campaign.starting_location_id = world.location_id    # the tempting fallback
        for cid in (world.kael_id, world.bront_id):
            (await s.get(Character, cid)).location_id = None

    with pytest.raises(StateIntegrityError):
        await SessionOpeningService(db, provider).resolve_opening_location(
            campaign_id=world.campaign_id,
            attendance_member_ids=[world.p1_member_id, world.p2_member_id])

    # The full command path reports it and changes NOTHING.
    game = build_bridge(db, provider=provider)
    admin = AdminBridge(db, provider, creation_flow=game.creation_flow,
                        session_zero=game.session_zero)
    result = await admin.handle(_admin_msg("!rv session start"))
    assert result.responses[0].kind == MessageKind.TECHNICAL_ERROR
    assert "จุดเริ่มต้น" in result.responses[0].content       # names the refused reset
    async with db.session() as s:
        active = [r for r in (await s.execute(select(Session).where(
            Session.campaign_id == world.campaign_id))).scalars()
            if r.status != SessionStatus.COMPLETE.value]
        assert active == []                                   # no session was created
    assert (await _char(db, world.kael_id)).location_id is None   # nobody teleported


async def test_new_session_never_teleports_a_positioned_character(db, provider):
    """Session 2 opens at the party anchor; a character who is elsewhere STAYS there
    (a split party is a fact, not an error to fix by teleport)."""
    world = await build_world(db)
    opening = await _open_session(db, provider, world, location_id=world.location_id)
    await _complete(db, opening.session_id)

    async with db.unit_of_work() as s:
        elsewhere = Location(campaign_id=world.campaign_id, name="หอคอยร้างกลางป่า")
        s.add(elsewhere)
        await s.flush()
        (await s.get(Character, world.bront_id)).location_id = elsewhere.id
        elsewhere_id = elsewhere.id

    second = await _open_session(db, provider, world)         # resolves via anchor
    assert second.number == 2
    assert (await _char(db, world.kael_id)).location_id == world.location_id
    # Bront was NOT dragged to the opening location.
    assert (await _char(db, world.bront_id)).location_id == elsewhere_id


async def test_late_joiner_is_placed_at_party_anchor_not_campaign_start(db, provider):
    """A friend joining mid-campaign appears WITH the party, never at the opening."""
    from app.models.enums import MemberRole
    from app.services.campaigns import CampaignService, CharacterService

    world = await build_world(db)
    opening = await _open_session(db, provider, world, location_id=world.location_id)
    await _complete(db, opening.session_id)

    # The party has travelled: anchor + positions now at the harbor, start stays put.
    async with db.unit_of_work() as s:
        harbor = Location(campaign_id=world.campaign_id, name="ท่าเรือเก่า")
        s.add(harbor)
        await s.flush()
        harbor_id = harbor.id
        campaign = await s.get(Campaign, world.campaign_id)
        campaign.starting_location_id = world.location_id
        campaign.current_party_anchor_id = harbor_id
        for cid in (world.kael_id, world.bront_id):
            (await s.get(Character, cid)).location_id = harbor_id
        # A new player joins and creates a character (no position yet).
        member = await CampaignService(s).add_member(
            campaign_id=world.campaign_id, discord_user_id="disc-p3",
            display_name="มายด์", role=MemberRole.PLAYER)
        newcomer = await CharacterService(s).create_character(
            member_id=member.id, name="Rin", species="human", char_class="wizard",
            abilities={"int": 15}, proficiencies=["arcana"], level=1, max_hp=8, ac=12)
        member_id, newcomer_id = member.id, newcomer.id

    second = await _open_session(
        db, provider, world,
        attendance=[world.p1_member_id, world.p2_member_id, member_id])
    assert second.number == 2
    joined = await _char(db, newcomer_id)
    assert joined.location_id == harbor_id                    # with the party
    assert joined.location_id != world.location_id            # NOT the campaign start
    # The veterans did not move either.
    assert (await _char(db, world.kael_id)).location_id == harbor_id


# --- cinematic opening: exactly once, governed by state not session number ---------

_GOAL = "ทำลายตราสัญญากลวงก่อนวันที่เก้า"


def _prologues(result):
    return [m for m in result.messages if m.kind == MessageKind.CAMPAIGN_PROLOGUE]


async def test_cinematic_plays_on_later_session_when_never_played(db, provider):
    """A campaign whose first session was consumed by a broken start still gets its
    opening cinematic on the NEXT session — and never again after that."""
    world = await build_world(db)
    # Session 1 happens with no main goal (the real playtest's broken start).
    first = await _open_session(db, provider, world, location_id=world.location_id)
    assert first.number == 1 and _prologues(first) == []
    await _complete(db, first.session_id)

    # The campaign canon arrives (import completes): a main goal now exists.
    async with db.unit_of_work() as s:
        (await s.get(Campaign, world.campaign_id)).central_question = _GOAL

    second = await _open_session(db, provider, world)
    assert second.number == 2
    assert _prologues(second)                                 # cinematic finally plays
    async with db.session() as s:
        campaign = await s.get(Campaign, world.campaign_id)
        assert campaign.config.get("opening_cinematic_played") is True
    await _complete(db, second.session_id)

    third = await _open_session(db, provider, world)
    assert _prologues(third) == []                            # never replays


async def test_start_at_still_plays_the_cinematic_exactly_once(db, provider):
    """An owner-supplied location must not bypass the opening cinematic."""
    world = await build_world(db)
    async with db.unit_of_work() as s:
        (await s.get(Campaign, world.campaign_id)).central_question = _GOAL
        location_name = (await s.get(Location, world.location_id)).name

    game = build_bridge(db, provider=provider)
    admin = AdminBridge(db, provider, creation_flow=game.creation_flow,
                        session_zero=game.session_zero)
    result = await admin.handle(_admin_msg(f"!rv session start at {location_name}"))
    kinds = [m.kind for m in result.responses]
    assert MessageKind.CAMPAIGN_PROLOGUE in kinds             # cinematic ran
    async with db.session() as s:
        assert (await s.get(Campaign, world.campaign_id)).config.get(
            "opening_cinematic_played") is True
