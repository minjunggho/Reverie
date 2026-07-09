"""Phase 2 acceptance: campaign -> members -> characters -> session -> scene, and
the one-active-character-per-member rule + rejected invalid transitions."""
from __future__ import annotations

import pytest

from app.core.errors import ConflictError, IllegalStateTransition
from app.models.enums import MemberRole, SessionStatus
from app.services.campaigns import CampaignService, CharacterService
from app.services.scenes import SceneService
from app.services.sessions import SessionService


async def _make_campaign(db):
    async with db.unit_of_work() as session:
        svc = CampaignService(session)
        campaign = await svc.create_campaign(
            name="เงามนตรา",
            discord_guild_id="guild-1",
            game_channel_id="chan-1",
            owner_discord_user_id="owner-1",
            owner_display_name="เจ้าของโต๊ะ",
        )
        return campaign.id


async def test_create_campaign_creates_owner_member(db):
    campaign_id = await _make_campaign(db)
    async with db.session() as session:
        svc = CampaignService(session)
        owner = await svc.resolve_member(campaign_id, "owner-1")
        assert owner is not None
        assert owner.role == MemberRole.OWNER.value


async def test_duplicate_channel_conflicts(db):
    await _make_campaign(db)
    with pytest.raises(ConflictError):
        async with db.unit_of_work() as session:
            await CampaignService(session).create_campaign(
                name="อื่น",
                discord_guild_id="guild-1",
                game_channel_id="chan-1",  # same channel
                owner_discord_user_id="owner-2",
                owner_display_name="x",
            )


async def test_add_members_and_resolve(db):
    campaign_id = await _make_campaign(db)
    async with db.unit_of_work() as session:
        svc = CampaignService(session)
        await svc.add_member(campaign_id=campaign_id, discord_user_id="p1", display_name="กี้")
        await svc.add_member(campaign_id=campaign_id, discord_user_id="p2", display_name="โบ")
    async with db.session() as session:
        svc = CampaignService(session)
        p1 = await svc.resolve_member(campaign_id, "p1")
        p2 = await svc.resolve_member(campaign_id, "p2")
        assert p1 is not None and p2 is not None and p1.id != p2.id
        # idempotent re-add
    async with db.unit_of_work() as session:
        again = await CampaignService(session).add_member(
            campaign_id=campaign_id, discord_user_id="p1", display_name="กี้"
        )
        assert again.id == p1.id


async def test_one_active_character_per_member(db):
    campaign_id = await _make_campaign(db)
    async with db.unit_of_work() as session:
        camp = CampaignService(session)
        chars = CharacterService(session)
        member = await camp.add_member(
            campaign_id=campaign_id, discord_user_id="p1", display_name="กี้"
        )
        c1 = await chars.create_character(member_id=member.id, name="Kael", char_class="rogue",
                                          abilities={"dex": 15}, proficiencies=["stealth"])
        member_id = member.id
        first_id = c1.id
    async with db.session() as session:
        member = await CampaignService(session).get_member(member_id)
        assert member.active_character_id == first_id

    # Create a second character but activate it explicitly — still exactly one active.
    async with db.unit_of_work() as session:
        chars = CharacterService(session)
        c2 = await chars.create_character(member_id=member_id, name="Mira", char_class="wizard",
                                          set_active=False)
        second_id = c2.id
    async with db.session() as session:
        member = await CampaignService(session).get_member(member_id)
        assert member.active_character_id == first_id  # unchanged
    async with db.unit_of_work() as session:
        await CharacterService(session).set_active_character(
            member_id=member_id, character_id=second_id
        )
    async with db.session() as session:
        member = await CampaignService(session).get_member(member_id)
        assert member.active_character_id == second_id  # replaced, still one


async def test_session_start_and_invalid_transition(db):
    campaign_id = await _make_campaign(db)
    async with db.unit_of_work() as session:
        svc = SessionService(session)
        s = await svc.create_session(campaign_id=campaign_id, attendance=["owner-1"])
        assert s.number == 1
        session_id = s.id
    async with db.unit_of_work() as session:
        s = await SessionService(session).start_session(session_id)
        assert s.status == SessionStatus.ACTIVE_PLAY.value
    # PREPARATION -> COMPLETE is illegal; and ACTIVE_PLAY -> COMPLETE directly is illegal.
    with pytest.raises(IllegalStateTransition):
        async with db.unit_of_work() as session:
            await SessionService(session).transition_status(session_id, SessionStatus.COMPLETE)


async def test_scene_create_and_pending_action(db):
    campaign_id = await _make_campaign(db)
    async with db.unit_of_work() as session:
        s = await SessionService(session).create_session(campaign_id=campaign_id)
        scene = await SceneService(session).create_scene(session_id=s.id, purpose="สำรวจโถงหน้า")
        scene_id = scene.id
        base_version = scene.version
    async with db.unit_of_work() as session:
        scene = await SceneService(session).get_scene(scene_id)
        await SceneService(session).set_pending_action(scene, {"id": "act-1", "text": "ทดสอบ"})
    async with db.session() as session:
        scene = await SceneService(session).get_scene(scene_id)
        assert scene.pending_action_id == "act-1"
        assert scene.version == base_version + 1
