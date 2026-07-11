"""E6 Activity API: authentication, authorization boundaries, and projection
correctness. Player payloads are inspected as serialized JSON — DM_ONLY material
must be absent from the bytes, not hidden by a client.
"""
from __future__ import annotations

import json
import time

import httpx
import pytest_asyncio
from sqlalchemy import select

import app.db.session as db_session_module
from app.auth.activity import mint_session_token, resolve_secret, verify_session_token
from app.core.config import get_settings
from app.main import create_app
from app.models.campaign import Campaign
from app.models.enums import EventType, KnowledgeStatus, MemberRole, SceneMode, Visibility
from app.models.knowledge import Secret
from app.models.location import Location
from app.models.npc import NPC
from app.services.campaigns import CampaignService, CharacterService
from app.services.events import EventService
from app.services.scenes import SceneService
from app.services.sessions import SessionService
from app.tabletop.rules.derive import skill_bonus


@pytest_asyncio.fixture
async def client(db):
    """ASGI test client with the Activity routes wired to the per-test database."""
    old = db_session_module._default_db
    db_session_module._default_db = db
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://activity.test") as c:
        yield c
    db_session_module._default_db = old


def _token(*, user_id: str, discord_user_id: str, name: str = "x",
           ttl: int = 60, now: float | None = None) -> str:
    return mint_session_token(
        resolve_secret(get_settings()), user_id=user_id, discord_user_id=discord_user_id,
        display_name=name, ttl_minutes=ttl, now=now)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _world(db):
    """Owner (Nick, wizard Daybell) + player (Mai, rogue Aria) at one location, in
    an active session/scene; a DM secret + clue + DM_ONLY event exist."""
    async with db.unit_of_work() as s:
        camp = CampaignService(s)
        chars = CharacterService(s)
        campaign = await camp.create_campaign(
            name="The Last Funeral of God", discord_guild_id="g1",
            game_channel_id="chan-act", owner_discord_user_id="disc-owner",
            owner_display_name="Nick")
        await camp.activate_campaign(campaign.id)
        owner = await camp.resolve_member(campaign.id, "disc-owner")
        player = await camp.add_member(campaign_id=campaign.id, discord_user_id="disc-player",
                                       display_name="Mai", role=MemberRole.PLAYER)
        daybell = await chars.create_character(
            member_id=owner.id, name="Daybell", char_class="wizard", species="human",
            abilities={"int": 16, "dex": 14, "con": 12},
            proficiencies=["arcana", "investigation"], max_hp=7, ac=12)
        aria = await chars.create_character(
            member_id=player.id, name="Aria", char_class="rogue", species="halfling",
            abilities={"dex": 16, "int": 13}, proficiencies=["stealth"], max_hp=9, ac=14)

        from app.world import LocationService

        loc = await LocationService(s).create_location(
            campaign_id=campaign.id, name="Old Crypt",
            description_obvious="ห้องใต้ดินหินเก่า เทียนไขล้อมโลงศพ")
        daybell.location_id = loc.id
        aria.location_id = loc.id

        s.add(Secret(campaign_id=campaign.id, fact="DMSECRET_พระเจ้าไม่ได้ตาย",
                     visibility=Visibility.DM_ONLY.value))

        sess = await SessionService(s).create_session(
            campaign_id=campaign.id, attendance=[owner.id, player.id])
        await SessionService(s).start_session(sess.id)
        scene = await SceneService(s).create_scene(
            session_id=sess.id, location_id=loc.id, mode=SceneMode.SOCIAL,
            participants=[f"character:{daybell.id}", f"character:{aria.id}"])

        events = EventService(s)
        await events.record(campaign_id=campaign.id, session_id=sess.id,
                            event_type=EventType.SCENE_STARTED, visibility=Visibility.PUBLIC,
                            payload={"summary": "เปิดฉากที่โบสถ์ดำ"})
        await events.record(campaign_id=campaign.id, session_id=sess.id,
                            event_type=EventType.NPC_STATE_CHANGED,
                            visibility=Visibility.DM_ONLY,
                            payload={"summary": "DMONLY_แม่ชีเริ่มสงสัยปาร์ตี้"})
        # Aria's private discovery — must never reach Daybell's payloads.
        await events.record(campaign_id=campaign.id, session_id=sess.id,
                            event_type=EventType.KNOWLEDGE_GAINED,
                            visibility=Visibility.PLAYER_ONLY,
                            witnesses=[f"character:{aria.id}"],
                            payload={"summary": "PRIVATE_ARIA_เห็นสัญลักษณ์ใต้โลง"})
        return {
            "campaign_id": campaign.id, "session_id": sess.id, "scene_id": scene.id,
            "owner_member": owner.id, "player_member": player.id,
            "daybell": daybell.id, "aria": aria.id, "loc": loc.id,
            "owner_user_id": owner.user_id, "player_user_id": player.user_id,
        }


# --- auth ------------------------------------------------------------------------

async def test_session_token_roundtrip_and_expiry(db):
    secret = "s3cret"
    tok = mint_session_token(secret, user_id="u1", discord_user_id="d1",
                             display_name="X", ttl_minutes=1)
    p = verify_session_token(secret, tok)
    assert p.user_id == "u1" and p.discord_user_id == "d1"
    expired = mint_session_token(secret, user_id="u1", discord_user_id="d1",
                                 display_name="X", ttl_minutes=1,
                                 now=time.time() - 3600)
    import pytest

    from app.auth.activity import ActivityAuthError
    with pytest.raises(ActivityAuthError):
        verify_session_token(secret, expired)
    with pytest.raises(ActivityAuthError):
        verify_session_token("other-secret", tok)


async def test_endpoints_require_authentication(db, client):
    w = await _world(db)
    r = await client.get(f"/api/activity/v1/campaigns/{w['campaign_id']}/grimoire/overview")
    assert r.status_code == 401
    r = await client.get("/api/activity/v1/context")
    assert r.status_code == 401
    # Expired token is rejected.
    tok = _token(user_id=w["owner_user_id"], discord_user_id="disc-owner",
                 now=time.time() - 999999)
    r = await client.get("/api/activity/v1/context", headers=_auth(tok))
    assert r.status_code == 401


async def test_auth_exchange_uses_server_side_oauth(db, client):
    from app.api.activity import router as router_module

    class FakeOAuth:
        def __init__(self, settings):
            pass

        async def exchange_and_identify(self, code):
            assert code == "good-code"
            return {"id": "disc-new", "display_name": "Newbie", "access_token": "at"}

    from app.api.activity.router import set_oauth_client_factory
    set_oauth_client_factory(FakeOAuth)
    try:
        r = await client.post("/api/activity/v1/auth/exchange", json={"code": "good-code"})
        assert r.status_code == 200
        body = r.json()
        assert body["session_token"] and body["discord_access_token"] == "at"
        p = verify_session_token(resolve_secret(get_settings()), body["session_token"])
        assert p.discord_user_id == "disc-new"
    finally:
        from app.auth.activity import DiscordOAuthClient
        set_oauth_client_factory(DiscordOAuthClient)


# --- context ----------------------------------------------------------------------

async def test_context_resolves_membership_and_channel(db, client):
    w = await _world(db)
    tok = _token(user_id=w["owner_user_id"], discord_user_id="disc-owner", name="Nick")
    r = await client.get("/api/activity/v1/context?channel_id=chan-act&guild_id=g1",
                         headers=_auth(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["campaign"]["name"] == "The Last Funeral of God"
    assert body["membership"]["role"] == "OWNER"
    assert body["membership"]["can_open_dm_studio"] is True
    assert body["character"]["name"] == "Daybell"
    assert body["session"]["active"] is True
    assert body["scene"]["location_name"] == "Old Crypt"
    # Forged guild context does not bind the campaign.
    r = await client.get("/api/activity/v1/context?channel_id=chan-act&guild_id=WRONG",
                         headers=_auth(tok))
    assert r.json()["campaign"] is None
    # A non-member sees no campaign and only their own campaign list (empty).
    async with db.unit_of_work() as s:
        stranger = await CampaignService(s).get_or_create_user("disc-stranger", "Strange")
        stranger_id = stranger.id
    tok2 = _token(user_id=stranger_id, discord_user_id="disc-stranger")
    r = await client.get("/api/activity/v1/context?channel_id=chan-act&guild_id=g1",
                         headers=_auth(tok2))
    assert r.json()["campaign"] is None
    assert r.json()["my_campaigns"] == []


# --- player Grimoire ----------------------------------------------------------------

async def test_player_reads_own_grimoire_with_engine_derived_numbers(db, client):
    w = await _world(db)
    tok = _token(user_id=w["player_user_id"], discord_user_id="disc-player", name="Mai")
    base = f"/api/activity/v1/campaigns/{w['campaign_id']}/grimoire"

    r = await client.get(f"{base}/overview", headers=_auth(tok))
    assert r.status_code == 200
    ov = r.json()
    assert ov["name"] == "Aria" and ov["hp"] == 9 and ov["ac"] == 14
    assert ov["location_name"] == "Old Crypt"

    r = await client.get(f"{base}/skills", headers=_auth(tok))
    sk = r.json()
    assert len(sk["skills"]) == 18
    async with db.session() as s:
        from app.models.character import Character
        aria = await s.get(Character, w["aria"])
        for row in sk["skills"]:
            assert row["total"] == skill_bonus(aria, row["key"]).total
    stealth = next(x for x in sk["skills"] if x["key"] == "stealth")
    assert stealth["proficiency"] == "PROFICIENT"
    assert {"label": "DEX", "value": 3} in stealth["breakdown"]

    r = await client.get(f"{base}/party", headers=_auth(tok))
    party = r.json()["members"]
    me = next(m for m in party if m["name"] == "Aria")
    other = next(m for m in party if m["name"] == "Daybell")
    assert "hp" in me and me["is_you"]
    assert "hp" not in other            # other players' exact HP is not exposed


async def test_player_payloads_contain_no_dm_or_foreign_private_data(db, client):
    w = await _world(db)
    tok = _token(user_id=w["owner_user_id"], discord_user_id="disc-owner", name="Nick")
    base = f"/api/activity/v1/campaigns/{w['campaign_id']}/grimoire"
    blobs = []
    for path in ("overview", "skills", "spellbook", "features", "inventory",
                 "story", "party", "chronicle"):
        r = await client.get(f"{base}/{path}", headers=_auth(tok))
        assert r.status_code == 200, path
        blobs.append(json.dumps(r.json(), ensure_ascii=False))
    joined = "\n".join(blobs)
    assert "DMSECRET_" not in joined            # DM secret text absent
    assert "DMONLY_" not in joined              # DM_ONLY event absent
    assert "PRIVATE_ARIA_" not in joined        # another player's PLAYER_ONLY absent


async def test_own_private_discovery_appears_in_owners_chronicle_only(db, client):
    w = await _world(db)
    base = f"/api/activity/v1/campaigns/{w['campaign_id']}/grimoire/chronicle"
    tok_aria = _token(user_id=w["player_user_id"], discord_user_id="disc-player")
    r = await client.get(base, headers=_auth(tok_aria))
    assert any("PRIVATE_ARIA_" in e["summary"] for e in r.json()["entries"])
    tok_nick = _token(user_id=w["owner_user_id"], discord_user_id="disc-owner")
    r = await client.get(base, headers=_auth(tok_nick))
    assert not any("PRIVATE_ARIA_" in e["summary"] for e in r.json()["entries"])


async def test_forged_campaign_id_is_rejected(db, client):
    w = await _world(db)
    async with db.unit_of_work() as s:
        other = await CampaignService(s).create_campaign(
            name="Other Table", discord_guild_id="g2", game_channel_id="chan-other",
            owner_discord_user_id="disc-somebody", owner_display_name="Some")
        other_id = other.id
    tok = _token(user_id=w["player_user_id"], discord_user_id="disc-player")
    r = await client.get(f"/api/activity/v1/campaigns/{other_id}/grimoire/overview",
                         headers=_auth(tok))
    assert r.status_code == 403
    r = await client.get("/api/activity/v1/campaigns/nope/grimoire/overview",
                         headers=_auth(tok))
    assert r.status_code == 404


# --- DM Studio authorization ----------------------------------------------------------

async def test_player_cannot_access_dm_studio_owner_can(db, client):
    w = await _world(db)
    studio = f"/api/activity/v1/campaigns/{w['campaign_id']}/studio"
    tok_player = _token(user_id=w["player_user_id"], discord_user_id="disc-player")
    for path in ("command-center", "scene", "world", "npcs", "threats",
                 "secrets", "events", "imports"):
        r = await client.get(f"{studio}/{path}", headers=_auth(tok_player))
        assert r.status_code == 403, path

    tok_owner = _token(user_id=w["owner_user_id"], discord_user_id="disc-owner")
    r = await client.get(f"{studio}/command-center", headers=_auth(tok_owner))
    assert r.status_code == 200
    assert r.json()["campaign"]["name"] == "The Last Funeral of God"
    r = await client.get(f"{studio}/secrets", headers=_auth(tok_owner))
    assert any("DMSECRET_" in s["fact"] for s in r.json()["secrets"])


async def test_dm_role_comes_from_database_not_frontend_flags(db, client):
    """Sending is_dm/role in headers/query changes nothing — role is a DB fact."""
    w = await _world(db)
    tok = _token(user_id=w["player_user_id"], discord_user_id="disc-player")
    r = await client.get(
        f"/api/activity/v1/campaigns/{w['campaign_id']}/studio/secrets?is_dm=true&role=OWNER",
        headers={**_auth(tok), "X-Reverie-Role": "OWNER"})
    assert r.status_code == 403


async def test_studio_npc_projection_separates_knowledge_from_canon(db, client):
    w = await _world(db)
    async with db.unit_of_work() as s:
        npc = NPC(campaign_id=w["campaign_id"], name="Mother Veyra",
                  personality="เย็นชา", voice_register="ต่ำ",
                  goals=["คุ้มกันโลง"], current_location_id=w["loc"],
                  communication_mode="SPOKEN")
        s.add(npc)
        await s.flush()
        from app.npcs.knowledge_service import NPCKnowledgeService
        await NPCKnowledgeService(s).add_fact(
            npc_id=npc.id, subject="party", fact="เชื่อว่าปาร์ตี้ปิดบังบางอย่าง",
            status=KnowledgeStatus.BELIEVES)
        npc_id = npc.id
    tok = _token(user_id=w["owner_user_id"], discord_user_id="disc-owner")
    r = await client.get(
        f"/api/activity/v1/campaigns/{w['campaign_id']}/studio/npcs/{npc_id}",
        headers=_auth(tok))
    body = r.json()
    assert body["npc"]["name"] == "Mother Veyra"          # objective canon block
    assert body["knowledge"][0]["status"] == "BELIEVES"    # epistemic block, separate
    assert "เชื่อว่า" in body["knowledge"][0]["fact"]
    assert body["npc"].get("knowledge") is None            # not flattened together


async def test_studio_scene_excludes_stale_npc_refs(db, client):
    w = await _world(db)
    async with db.unit_of_work() as s:
        elsewhere = Location(campaign_id=w["campaign_id"], name="Chapel Road")
        s.add(elsewhere)
        await s.flush()
        npc = NPC(campaign_id=w["campaign_id"], name="Wanderer",
                  current_location_id=elsewhere.id)
        s.add(npc)
        await s.flush()
        from app.models.scene import Scene
        scene = await s.get(Scene, w["scene_id"])
        scene.visible_entity_ids = [f"npc:{npc.id}"]
    tok = _token(user_id=w["owner_user_id"], discord_user_id="disc-owner")
    r = await client.get(f"/api/activity/v1/campaigns/{w['campaign_id']}/studio/scene",
                         headers=_auth(tok))
    body = r.json()
    assert body["present_npcs"] == []
    assert any("Wanderer" in sr["reason"] for sr in body["stale_refs"])


# --- import mutations -------------------------------------------------------------------

async def test_import_lifecycle_through_activity_api(db, client):
    from pathlib import Path
    w = await _world(db)
    fixture = (Path(__file__).parent / "fixtures" / "last_funeral_of_god.md").read_bytes()
    async with db.unit_of_work() as s:
        from app.services.campaigns.canon_import import CanonImportService
        draft = await CanonImportService(s).create_draft(
            campaign_id=w["campaign_id"], uploader_member_id=w["owner_member"],
            filename="lfog.md", data=fixture)
        draft_id = draft.id

    studio = f"/api/activity/v1/campaigns/{w['campaign_id']}/studio"
    tok_player = _token(user_id=w["player_user_id"], discord_user_id="disc-player")
    tok_owner = _token(user_id=w["owner_user_id"], discord_user_id="disc-owner")

    # Player cannot see or act on imports.
    assert (await client.get(f"{studio}/imports", headers=_auth(tok_player))).status_code == 403
    r = await client.post(f"{studio}/imports/{draft_id}/approve", headers=_auth(tok_player))
    assert r.status_code == 403

    # Owner sees the pending import and approves it through the domain service.
    r = await client.get(f"{studio}/imports", headers=_auth(tok_owner))
    assert r.json()["imports"][0]["status"] == "PENDING_REVIEW"
    r = await client.post(f"{studio}/imports/{draft_id}/approve", headers=_auth(tok_owner))
    assert r.status_code == 200
    assert r.json()["counts"]["locations"] == 7
    async with db.session() as s:
        count = len(list((await s.execute(
            select(Location).where(Location.campaign_id == w["campaign_id"]))).scalars()))
        assert count == 8          # 7 imported + the pre-existing Black Chapel
    # Second approve conflicts (already approved) — surfaced as 409, not a 500.
    r = await client.post(f"{studio}/imports/{draft_id}/approve", headers=_auth(tok_owner))
    assert r.status_code == 409
