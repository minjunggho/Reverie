"""Player-facing explicit follow consent through the real ``!rv`` bridge.

The command may mutate only the requester's active character. Target lookup is
campaign-scoped, exact after Unicode-safe normalization, and never accepts an id.
Travel itself remains owned by ``TravelService``/``PositionService``.
"""
from __future__ import annotations

from app.discord_bridge import AdminBridge, InboundMessage
from app.models.character import Character
from app.models.enums import MemberRole
from app.services.campaigns import CampaignService, CharacterService
from app.world import LocationService, PositionService
from tests.support.factories import build_world

_counter = 0


def _message(
    content: str,
    *,
    author: str = "disc-p1",
    name: str = "กี้",
    channel: str = "chan-1",
) -> InboundMessage:
    global _counter
    _counter += 1
    return InboundMessage(
        discord_message_id=f"follow-{_counter}",
        guild_id="guild-1",
        channel_id=channel,
        author_discord_id=author,
        author_display_name=name,
        content=content,
    )


async def _co_located_world(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        kael = await s.get(Character, world.kael_id)
        bront = await s.get(Character, world.bront_id)
        kael.location_id = world.location_id
        bront.location_id = world.location_id
    return world


async def test_follow_co_located_character_persists_through_existing_state(db, provider):
    world = await _co_located_world(db)
    result = await AdminBridge(db, provider).handle(
        _message("!rv follow   BRONT  "))

    body = result.responses[0].content
    assert "Kael" in body and "Bront" in body
    assert "เดินทางตาม" in body
    assert world.kael_id not in body and world.bront_id not in body
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).following_character_id == world.bront_id
        assert (await s.get(Character, world.bront_id)).following_character_id is None


async def test_unfollow_after_fresh_bridge_clears_only_requester(db, provider):
    world = await _co_located_world(db)
    await AdminBridge(db, provider).handle(_message("!rv follow Bront"))

    # A fresh bridge simulates the command process being reconstructed. The follow
    # consent comes from Character.following_character_id, not bridge memory.
    result = await AdminBridge(db, provider).handle(_message("!rv unfollow"))
    body = result.responses[0].content
    assert "Kael" in body and "Bront" in body and "ไม่ได้ตาม" in body
    assert world.kael_id not in body and world.bront_id not in body
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).following_character_id is None
        assert (await s.get(Character, world.bront_id)).following_character_id is None


async def test_cannot_follow_self_even_with_normalized_case(db, provider):
    world = await _co_located_world(db)
    result = await AdminBridge(db, provider).handle(
        _message("!rv follow   kAeL   "))

    assert "ไม่สามารถตามตัวเอง" in result.responses[0].content
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).following_character_id is None


async def test_cannot_follow_character_at_another_location(db, provider):
    world = await _co_located_world(db)
    async with db.unit_of_work() as s:
        elsewhere = await LocationService(s).create_location(
            campaign_id=world.campaign_id,
            name="ลานอีกฝั่ง",
            description_obvious="อยู่อีกฟากของกำแพง",
        )
        (await s.get(Character, world.bront_id)).location_id = elsewhere.id

    result = await AdminBridge(db, provider).handle(_message("!rv follow Bront"))

    assert "อยู่ที่เดียว" in result.responses[0].content
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).following_character_id is None


async def test_cannot_follow_character_from_another_campaign(db, provider):
    world = await _co_located_world(db)
    async with db.unit_of_work() as s:
        campaigns = CampaignService(s)
        other = await campaigns.create_campaign(
            name="อีกโต๊ะ",
            discord_guild_id="guild-1",
            game_channel_id="chan-2",
            owner_discord_user_id="owner-2",
            owner_display_name="DM2",
        )
        outsider_member = await campaigns.add_member(
            campaign_id=other.id,
            discord_user_id="disc-outsider",
            display_name="คนนอก",
            role=MemberRole.PLAYER,
        )
        outsider = await CharacterService(s).create_character(
            member_id=outsider_member.id,
            name="Outsider",
            char_class="fighter",
        )
        other_location = await LocationService(s).create_location(
            campaign_id=other.id,
            name="สถานที่อีกแคมเปญ",
            description_obvious="ไม่เกี่ยวข้องกับโต๊ะแรก",
        )
        outsider.location_id = other_location.id
        outsider_id = outsider.id

    result = await AdminBridge(db, provider).handle(_message("!rv follow Outsider"))

    assert "ไม่พบตัวละคร" in result.responses[0].content
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).following_character_id is None
        assert (await s.get(Character, outsider_id)).following_character_id is None


async def test_ambiguous_exact_character_name_requests_clarification(db, provider):
    world = await _co_located_world(db)
    async with db.unit_of_work() as s:
        campaigns = CampaignService(s)
        member = await campaigns.add_member(
            campaign_id=world.campaign_id,
            discord_user_id="disc-p3",
            display_name="ซี",
            role=MemberRole.PLAYER,
        )
        duplicate = await CharacterService(s).create_character(
            member_id=member.id,
            name="bront",
            char_class="fighter",
        )
        duplicate.location_id = world.location_id
        duplicate_id = duplicate.id

    result = await AdminBridge(db, provider).handle(_message("!rv follow BRONT"))
    body = result.responses[0].content

    assert "มากกว่าหนึ่งคน" in body and "โปรดใช้ชื่อเต็ม" in body
    assert world.bront_id not in body and duplicate_id not in body
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).following_character_id is None


async def test_unfollow_payload_cannot_clear_another_players_state(db, provider):
    world = await _co_located_world(db)
    async with db.unit_of_work() as s:
        await PositionService(s).set_follow(
            follower_id=world.bront_id, leader_id=world.kael_id)

    # A payload that tries to name another character is rejected. Neither follow
    # state is changed, and Bront's consent remains untouched.
    result = await AdminBridge(db, provider).handle(_message("!rv unfollow Bront"))

    assert "เฉพาะตัวละครของเจ้า" in result.responses[0].content
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).following_character_id is None
        assert (await s.get(Character, world.bront_id)).following_character_id == world.kael_id


async def test_follow_requires_an_active_player_character_target(db, provider):
    world = await _co_located_world(db)
    async with db.unit_of_work() as s:
        inactive = await CharacterService(s).create_character(
            member_id=world.p2_member_id,
            name="พักไว้ก่อน",
            char_class="fighter",
            set_active=False,
        )
        inactive.location_id = world.location_id
        inactive_id = inactive.id

    result = await AdminBridge(db, provider).handle(_message("!rv follow พักไว้ก่อน"))

    assert "ไม่พบตัวละคร" in result.responses[0].content
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).following_character_id is None
        assert (await s.get(Character, inactive_id)).following_character_id is None
