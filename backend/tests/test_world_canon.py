"""E5 world-canon + navigation: import review/commit, canonical travel (the
walk-outside fix), location persistence, AI world expansion, anti-hallucination,
world-authoring-question rejection, world pressure, and secret safety."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from app.ai.narration_guard import (
    is_world_authoring_question,
    screen_decision_prompt,
    screen_narration,
)
from app.core.randomness import SequenceRandomness
from app.discord_bridge import AdminBridge, InboundAttachment, InboundMessage
from app.engine import build_bridge
from app.models.campaign import Campaign
from app.models.character import Character
from app.models.enums import Visibility
from app.models.knowledge import Secret
from app.models.location import Location
from app.models.npc import NPC
from app.models.scene import Scene
from app.models.world import Threat
from app.models.world_graph import CampaignCanonRecord, LocationConnection
from app.presentation import MessageKind
from app.schemas.llm_io import ActionInterpretation
from app.services.campaigns import CampaignService, CharacterService
from app.services.sessions import SessionOpeningService

_FIXTURE = (Path(__file__).parent / "fixtures" / "last_funeral_of_god.md").read_bytes()
_n = {"v": 0}


def _msg(content, *, author="owner", attachment=None):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"w{_n['v']}", guild_id="g", channel_id="chan-1",
        author_discord_id=author, author_display_name=author, content=content,
        attachments=(attachment,) if attachment else ())


# --- anti-hallucination unit --------------------------------------------------------

def test_world_authoring_questions_are_detected_and_rewritten():
    assert is_world_authoring_question("เมื่อออกไปข้างนอก เจ้าเห็นอะไร?")
    assert is_world_authoring_question("ร้านนี้มีหน้าตาแบบไหน?")
    assert is_world_authoring_question("มีใครอยู่ในตรอก?")
    assert is_world_authoring_question("What do you see outside?")
    # Character-facing questions are fine.
    assert not is_world_authoring_question("Veskan จะทำอย่างไร?")
    assert not is_world_authoring_question("เจ้าจะพูดอะไรกับยาม?")
    # Rewrite.
    assert screen_decision_prompt("เจ้าเห็นอะไรข้างนอก?", "Veskan") == "Veskanจะทำอย่างไร?"
    text, changed = screen_narration("ฝนตกหนัก\nข้างนอกมีอะไร?", "Veskan")
    assert changed and "มีอะไร" not in text and "ฝนตกหนัก" in text


# --- the full playtest --------------------------------------------------------------

async def _import_last_funeral(admin, db):
    await admin.handle(_msg("!rv campaign new The Last Funeral of God"))
    r = await admin.handle(_msg(
        "!rv campaign import",
        attachment=InboundAttachment("lfog.md", "text/markdown", _FIXTURE)))
    body = r.responses[0].content
    async with db.session() as s:
        from app.models.canon_import import CanonImport
        draft = (await s.execute(select(CanonImport))).scalar_one()
    return draft, body


async def test_import_review_identifies_all_sections(db, provider):
    admin = AdminBridge(db, provider)
    draft, body = await _import_last_funeral(admin, db)
    review = draft.proposal["_review"]["counts"]
    assert review["locations"] == 7
    assert review["important_npcs"] == 6
    assert review["secrets"] == 2
    assert review["clues"] >= 4
    assert review["factions"] == 1
    assert review["threats"] == 1
    assert review["protocols"] == 1
    assert review["session_prep"] == 1
    assert review["world_facts"] == 3
    # Warnings surface real structural gaps (Seraphine has no location).
    warnings = " ".join(draft.proposal["_review"]["warnings"])
    assert "Mother Seraphine" in warnings
    # Nothing is canon before approval.
    async with db.session() as s:
        assert (await s.execute(select(func.count(Location.id)))).scalar_one() == 0


async def test_approve_commits_canon_atomically(db, provider):
    admin = AdminBridge(db, provider)
    draft, _ = await _import_last_funeral(admin, db)
    await admin.handle(_msg(f"!rv campaign import approve {draft.id}"))
    async with db.session() as s:
        locs = list((await s.execute(select(Location))).scalars())
        assert len(locs) == 7
        assert all(l.provenance == "IMPORTED" for l in locs)
        # Geography: tavern's parent is Ash Quarter.
        tavern = next(l for l in locs if l.name == "Grey Wolf Tavern")
        ash = next(l for l in locs if l.name == "Ash Quarter")
        assert tavern.parent_id == ash.id
        # Travel graph edges exist (tavern <-> street).
        conns = list((await s.execute(select(LocationConnection))).scalars())
        assert any(c.from_location_id == tavern.id and c.travel_minutes == 0 for c in conns)
        # NPCs at their canonical location; secrets are DM-only; clues are canon records.
        npcs = list((await s.execute(select(NPC))).scalars())
        courier = next(n for n in npcs if n.name == "Church Courier")
        assert courier.current_location_id == tavern.id
        secrets = list((await s.execute(select(Secret))).scalars())
        assert any("พระเจ้าไม่ได้ตาย" in sec.fact for sec in secrets)
        assert all(sec.visibility == Visibility.DM_ONLY.value for sec in secrets)
        clues = list((await s.execute(select(CampaignCanonRecord).where(
            CampaignCanonRecord.category == "clue"))).scalars())
        assert len(clues) >= 4
        # Faction + threat became world-pressure fronts.
        threats = list((await s.execute(select(Threat))).scalars())
        assert {"The Last Church", "The Failing Seal"} <= {t.name for t in threats}
        # Campaign brief + session prep stored.
        campaign = (await s.execute(select(Campaign))).scalar_one()
        assert "พระเจ้าสิ้นลม" in campaign.brief
        assert campaign.session_prep["opening_location_id"] == tavern.id


async def _party_and_session(db, provider, admin):
    """Import+approve, add Veskan+Aria, start Session 1. Returns ids."""
    draft, _ = await _import_last_funeral(admin, db)
    await admin.handle(_msg(f"!rv campaign import approve {draft.id}"))
    async with db.unit_of_work() as s:
        camp = CampaignService(s)
        campaign = await camp.resolve_campaign_by_channel("chan-1")
        campaign.config = {
            **campaign.config,
            "dice_mode": "AUTO",
            # These travel tests exercise the legacy per-action deterministic pipeline.
            "planning": {"enabled": "off"},
        }
        mA = await camp.resolve_member(campaign.id, "owner")
        mB = await camp.add_member(campaign_id=campaign.id, discord_user_id="userB",
                                   display_name="Friend")
        chars = CharacterService(s)
        veskan = await chars.create_character(member_id=mA.id, name="Veskan", char_class="wizard")
        aria = await chars.create_character(member_id=mB.id, name="Aria", char_class="rogue")
        cid = campaign.id
        members = [mA.id, mB.id]
    opener = SessionOpeningService(db, provider)
    opening = await opener.open_new_session(
        campaign_id=cid, attendance_member_ids=members, location_id="",
        channel_id="chan-1")
    return cid, opening, veskan.id, aria.id


async def test_session1_opens_at_imported_location_with_prep(db, provider):
    admin = AdminBridge(db, provider)
    cid, opening, veskan_id, aria_id = await _party_and_session(db, provider, admin)
    # Opened at the tavern (imported prep), not a generic random place. The canonical
    # location shows in the session-title footer; the scene points at it.
    blob = " ".join((m.title or "") + str(m.data) for m in opening.messages)
    assert "Grey Wolf Tavern" in blob
    async with db.session() as s:
        scene = await s.get(Scene, opening.scene_id)
        loc = await s.get(Location, scene.location_id)
        assert loc.name == "Grey Wolf Tavern"
        # Imported allowed clues seeded the scene.
        assert any("เอกสาร" in c for c in (scene.allowed_clues or []))
        # Present NPCs from prep are visible; characters are physically placed.
        assert scene.visible_entity_ids
        veskan = await s.get(Character, veskan_id)
        assert veskan.location_id == loc.id


async def test_walking_outside_transitions_to_canonical_location(db, provider):
    admin = AdminBridge(db, provider)
    cid, opening, veskan_id, aria_id = await _party_and_session(db, provider, admin)
    # The interpreter flags movement; the ENGINE resolves the destination.
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ออกไปข้างนอก", method="เดินออกประตูหน้า", intent_confidence=0.9,
        movement_intent=True, movement_reference="ออกไปข้างนอก"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมเดินออกไปข้างนอก", author="owner"))
    assert r.state_mutated and "Bellmaker Street" in r.note
    frame = r.responses[0]
    assert frame.kind == MessageKind.SCENE_FRAME
    assert frame.title == "Bellmaker Street"
    assert "ถนนหินเปียกฝน" in frame.content          # canonical destination description
    # NEVER asked the player to author the world.
    assert not is_world_authoring_question(frame.content)
    assert not is_world_authoring_question(frame.data.get("decision_prompt", ""))
    async with db.session() as s:
        veskan = await s.get(Character, veskan_id)
        street = (await s.execute(select(Location).where(
            Location.name == "Bellmaker Street"))).scalar_one()
        assert veskan.location_id == street.id          # canonical position moved
        from app.services.scenes import SceneService

        old_scene = await s.get(Scene, opening.scene_id)
        assert old_scene.status == "CLOSED"              # a REAL transition, not an in-place mutation
        new_scene = await SceneService(s).get_active_scene(opening.session_id)
        assert new_scene.id != opening.scene_id
        assert new_scene.location_id == street.id
        # Travel dragged the party anchor — the next session opens HERE, not at
        # the imported starting location (E7 continuity).
        campaign = await s.get(Campaign, cid)
        assert campaign.current_party_anchor_id == street.id
    # Elapsed/current time is visible on the arrival frame.
    assert "วันที่ 1" in frame.data.get("footer", "")


async def test_left_behind_character_does_not_teleport_with_the_party(db, provider):
    admin = AdminBridge(db, provider)
    cid, opening, veskan_id, aria_id = await _party_and_session(db, provider, admin)
    # Aria stayed somewhere else earlier (canonical position differs from the actor's).
    async with db.unit_of_work() as s:
        cathedral = (await s.execute(select(Location).where(
            Location.name == "Cathedral District"))).scalar_one()
        aria = await s.get(Character, aria_id)
        aria.location_id = cathedral.id
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ออกไปข้างนอก", method="เดินออกประตูหน้า", intent_confidence=0.9,
        movement_intent=True, movement_reference="ออกไปข้างนอก"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมเดินออกไปข้างนอก", author="owner"))
    assert "Bellmaker Street" in r.note
    async with db.session() as s:
        street = (await s.execute(select(Location).where(
            Location.name == "Bellmaker Street"))).scalar_one()
        veskan = await s.get(Character, veskan_id)
        aria = await s.get(Character, aria_id)
        assert veskan.location_id == street.id           # the actor moved
        cathedral = (await s.execute(select(Location).where(
            Location.name == "Cathedral District"))).scalar_one()
        assert aria.location_id == cathedral.id          # the absent friend did NOT


async def test_travel_up_advances_time_and_ticks_threats(db, provider):
    admin = AdminBridge(db, provider)
    cid, opening, veskan_id, aria_id = await _party_and_session(db, provider, admin)
    # Move to the street first (0 min), then uphill to the cathedral (15 min).
    async with db.unit_of_work() as s:
        veskan = await s.get(Character, veskan_id)
        street = (await s.execute(select(Location).where(
            Location.name == "Bellmaker Street"))).scalar_one()
        veskan.location_id = street.id
        scene = await s.get(Scene, opening.scene_id)
        scene.location_id = street.id
        # A threat due soon so travel-time ticks it.
        threat = (await s.execute(select(Threat).where(Threat.name == "The Failing Seal"))).scalar_one()
        threat.scheduled_game_time = 5
        before = (await s.get(Campaign, cid)).current_game_time
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ไปมหาวิหาร", method="เดินขึ้นเนิน", intent_confidence=0.9,
        movement_intent=True, movement_reference="ขึ้นไปทางมหาวิหาร"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมเดินขึ้นไปทางมหาวิหาร", author="owner"))
    assert "Cathedral District" in r.note
    async with db.session() as s:
        after = (await s.get(Campaign, cid)).current_game_time
        assert after == before + 15                     # world time advanced by travel
        threat = (await s.execute(select(Threat).where(Threat.name == "The Failing Seal"))).scalar_one()
        assert threat.progress > 20                     # world pressure continued


async def test_ai_world_expansion_creates_persistent_location(db, provider):
    admin = AdminBridge(db, provider)
    cid, opening, veskan_id, aria_id = await _party_and_session(db, provider, admin)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="หาร้านขายกระดาษ", method="เดินหาร้าน", intent_confidence=0.9,
        movement_intent=True, movement_reference="ร้านขายกระดาษแถวนี้"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมหาร้านขายกระดาษแถวนี้", author="owner"))
    assert r.state_mutated and r.responses[0].kind == MessageKind.SCENE_FRAME
    async with db.session() as s:
        shop = (await s.execute(select(Location).where(
            Location.provenance == "AI_EXPANDED"))).scalars().first()
        assert shop is not None
        shop_id, shop_name = shop.id, shop.name
        count_before = (await s.execute(select(func.count(Location.id)))).scalar_one()

    # Return later (a second request) — the SAME persistent location, not a new one.
    async with db.unit_of_work() as s:  # walk back to the tavern to re-approach
        veskan = await s.get(Character, veskan_id)
        veskan.location_id = (await s.execute(select(Location).where(
            Location.name == "Grey Wolf Tavern"))).scalar_one().id
    r = await bridge.handle_inbound(_msg("! ผมกลับไปหาร้านขายกระดาษเดิม", author="owner"))
    async with db.session() as s:
        count_after = (await s.execute(select(func.count(Location.id)))).scalar_one()
        assert count_after == count_before             # no duplicate world
        again = await s.get(Location, shop_id)
        assert again.name == shop_name                 # identity preserved


async def test_scene_narration_never_asks_player_to_author_world(db, provider):
    """Even if the narrator model tries to ask 'what do you see?', the engine screens
    it out of committed narration."""
    admin = AdminBridge(db, provider)
    cid, opening, veskan_id, aria_id = await _party_and_session(db, provider, admin)
    from app.schemas.llm_io import Narration
    provider.on("generate_dm_narration", lambda m, model: Narration(
        text="เจ้าเปิดหนังสือ\nข้างนอกหน้าต่างมีอะไร?", style="concise",
        decision_prompt="เจ้าเห็นอะไรในห้อง?"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมนั่งอ่านหนังสือเวท", author="owner"))
    body = r.responses[0].content
    prompt = r.responses[0].data.get("decision_prompt", "")
    assert not is_world_authoring_question(body)
    assert not is_world_authoring_question(prompt)


async def test_import_does_not_leak_secrets_into_player_context(db, provider):
    admin = AdminBridge(db, provider)
    cid, opening, veskan_id, aria_id = await _party_and_session(db, provider, admin)
    from app.memory.scene_context import SceneContextBuilder
    async with db.session() as s:
        scene = await s.get(Scene, opening.scene_id)
        sctx = await SceneContextBuilder(s).build(
            campaign_id=cid, scene=scene, actor_character_id=veskan_id)
    blob = sctx.location_block()
    assert "Grey Wolf" in blob                          # canonical location present
    assert "พระเจ้าไม่ได้ตาย" not in blob                # the DM secret is not
