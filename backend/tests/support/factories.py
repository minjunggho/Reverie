"""Reusable test-world builders. Keep the canonical §34 setup in one place."""
from __future__ import annotations

from dataclasses import dataclass

from app.db.session import Database
from app.models.enums import MemberRole, SceneMode
from app.npcs import NPCService
from app.services.campaigns import CampaignService, CharacterService
from app.services.scenes import SceneService
from app.services.sessions import SessionService
from app.world import LocationService


@dataclass
class World:
    campaign_id: str
    channel_id: str
    owner_member_id: str
    p1_member_id: str
    p2_member_id: str
    p1_discord_id: str
    p2_discord_id: str
    kael_id: str          # p1's active character (rogue, dex-focused, stealth)
    bront_id: str         # p2's active character (fighter)
    location_id: str
    guard_npc_id: str


async def build_world(db: Database) -> World:
    """Create the canonical starting setup: campaign, two players + characters, a
    location, and one guard NPC. Session/scene are created separately per test."""
    async with db.unit_of_work() as session:
        camp = CampaignService(session)
        chars = CharacterService(session)
        npcs = NPCService(session)
        locs = LocationService(session)

        campaign = await camp.create_campaign(
            name="เงาแห่งนครเก่า",
            discord_guild_id="guild-1",
            game_channel_id="chan-1",
            owner_discord_user_id="owner-1",
            owner_display_name="DM",
        )
        await camp.activate_campaign(campaign.id)
        # Legacy factory worlds resolve checks immediately (AUTO); the dice-ritual
        # (PLAYER_CLICK, the production default) has its own dedicated tests.
        campaign.config = {**(campaign.config or {}), "dice_mode": "AUTO"}

        p1 = await camp.add_member(
            campaign_id=campaign.id, discord_user_id="disc-p1", display_name="กี้",
            role=MemberRole.PLAYER,
        )
        p2 = await camp.add_member(
            campaign_id=campaign.id, discord_user_id="disc-p2", display_name="โบ",
            role=MemberRole.PLAYER,
        )
        owner = await camp.resolve_member(campaign.id, "owner-1")

        kael = await chars.create_character(
            member_id=p1.id, name="Kael", species="halfling", char_class="rogue",
            abilities={"dex": 16, "wis": 12, "int": 13}, proficiencies=["stealth", "perception"],
            level=1, max_hp=9, ac=14,
        )
        bront = await chars.create_character(
            member_id=p2.id, name="Bront", species="dwarf", char_class="fighter",
            abilities={"str": 16, "con": 15}, proficiencies=["athletics"],
            level=1, max_hp=13, ac=16,
        )

        location = await locs.create_location(
            campaign_id=campaign.id, name="โถงหน้าคฤหาสน์",
            description_obvious="โถงกว้าง มีหน้าต่างบานใหญ่ทางทิศตะวันตก และประตูไม้เก่า",
            description_hidden="มีช่องลับหลังภาพวาดใหญ่",
        )
        guard = await npcs.create_npc(
            campaign_id=campaign.id, name="ยามเฝ้าประตู",
            personality="ขี้เบื่อ ระแวงเล็กน้อย", voice_register="ห้วน สั้น",
            current_location_id=location.id,
        )

        return World(
            campaign_id=campaign.id,
            channel_id="chan-1",
            owner_member_id=owner.id,
            p1_member_id=p1.id,
            p2_member_id=p2.id,
            p1_discord_id="disc-p1",
            p2_discord_id="disc-p2",
            kael_id=kael.id,
            bront_id=bront.id,
            location_id=location.id,
            guard_npc_id=guard.id,
        )


async def start_session_with_scene(db: Database, world: World) -> tuple[str, str]:
    """Start Session 1 and open an initial exploration scene. Returns (session_id, scene_id)."""
    async with db.unit_of_work() as session:
        sessions = SessionService(session)
        scenes = SceneService(session)
        s = await sessions.create_session(
            campaign_id=world.campaign_id, attendance=[world.p1_member_id, world.p2_member_id]
        )
        await sessions.start_session(s.id)
        scene = await scenes.create_scene(
            session_id=s.id, location_id=world.location_id, mode=SceneMode.EXPLORATION,
            purpose="หาทางเข้าไปในคฤหาสน์โดยไม่ให้ยามรู้ตัว",
            dramatic_question="พวกเขาจะเข้าไปได้โดยไม่ถูกจับได้ไหม",
            participants=[f"character:{world.kael_id}", f"character:{world.bront_id}"],
            visible_entity_ids=[f"npc:{world.guard_npc_id}"],
            immediate_threat_ids=[f"npc:{world.guard_npc_id}"],
        )
        return s.id, scene.id
