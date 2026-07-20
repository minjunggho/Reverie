"""!rv campaign reset — start a fresh world in the SAME channel, keeping characters.

The world/story/progress is wiped in place; players, their characters, and each
character's build survive. Owner-only, and destructive so it requires a confirm step.
"""
from __future__ import annotations

from app.discord_bridge import AdminBridge, InboundMessage
from app.models.campaign import Campaign, CampaignMember
from app.models.character import Character
from app.models.enums import CampaignStatus
from app.models.location import Location
from app.models.npc import NPC
from app.models.scene import Scene
from app.models.session import Session
from app.services.campaigns import CampaignService
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, *, author="owner-1", name="DM"):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"reset-{_n['v']}", guild_id="g", channel_id="chan-1",
        author_discord_id=author, author_display_name=name, content=content)


async def test_reset_keeps_characters_and_players_but_wipes_the_world(db, provider):
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        kael = await s.get(Character, world.kael_id)
        kael.hp = 1
        kael.conditions = ["poisoned"]
        kael.location_id = world.location_id
    admin = AdminBridge(db, provider)

    # Step 1: no `confirm` → warning, nothing is wiped.
    warn = await admin.handle(_msg("!rv campaign reset"))
    assert "ยืนยัน" in warn.responses[0].content
    async with db.session() as s:
        assert await s.get(Session, sid) is not None      # still there

    # Step 2: confirm → wipe.
    done = await admin.handle(_msg("!rv campaign reset confirm"))
    assert "รีเซ็ต" in (done.responses[0].title or "")

    async with db.session() as s:
        # Players + characters + build survive.
        assert await s.get(Character, world.kael_id) is not None
        assert await s.get(Character, world.bront_id) is not None
        assert await s.get(CampaignMember, world.p1_member_id) is not None
        assert await s.get(CampaignMember, world.p2_member_id) is not None
        # World / story / play is gone.
        assert await s.get(Session, sid) is None
        assert await s.get(Scene, scene_id) is None
        assert await s.get(NPC, world.guard_npc_id) is None
        assert await s.get(Location, world.location_id) is None
        # Campaign stays in the SAME channel, reset to a clean SETUP state.
        campaign = await s.get(Campaign, world.campaign_id)
        assert campaign is not None
        assert campaign.status == CampaignStatus.SETUP.value
        assert campaign.current_game_time == 0
        assert campaign.starting_location_id is None
        # Each character is returned to a clean, rested play-state (build untouched).
        kael = await s.get(Character, world.kael_id)
        assert kael.hp == kael.max_hp
        assert kael.conditions == []
        assert kael.location_id is None


async def test_channel_still_resolves_to_the_same_campaign_after_reset(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    admin = AdminBridge(db, provider)
    await admin.handle(_msg("!rv campaign reset confirm"))
    # The channel is not locked to a dead campaign — it still maps to this one, now fresh,
    # ready for `!rv campaign create` / `import` + `!rv session start`.
    async with db.session() as s:
        camp = await CampaignService(s).resolve_campaign_by_channel("chan-1")
    assert camp is not None and camp.id == world.campaign_id


async def test_reset_is_owner_only(db, provider):
    world = await build_world(db)
    sid, _ = await start_session_with_scene(db, world)
    admin = AdminBridge(db, provider)
    # A player (not the owner) cannot reset — even with confirm.
    r = await admin.handle(_msg("!rv campaign reset confirm", author=world.p1_discord_id, name="กี้"))
    assert "เจ้าของโต๊ะ" in r.responses[0].content
    async with db.session() as s:
        assert await s.get(Session, sid) is not None      # nothing was wiped
