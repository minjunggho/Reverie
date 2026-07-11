"""P0 playtest correctness fix — regression suite (Last Funeral of God, extended).

Real multiplayer playtest failures this pins: NPCs inventing facts/rules that
contradict imported canon; NPCs ignoring established communication style; NPCs
"teleporting" to every location the party visits; local movement/following a sound
wrongly minting new Locations; natural-language rest never reaching RestService.
"""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import func, select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import AdminBridge, InboundAttachment, InboundMessage
from app.engine import build_bridge
from app.entities import SceneEntityDirectory
from app.models.campaign import Campaign
from app.models.canon_import import CanonImport
from app.models.character import Character
from app.models.enums import SceneMode
from app.models.location import Location
from app.models.npc import NPC
from app.models.scene import Scene
from app.models.world import ScheduledWorldEvent
from app.schemas.llm_io import ActionInterpretation, NPCResponse
from app.services.campaigns import CampaignService, CharacterService
from app.services.scenes import SceneService
from app.services.sessions import SessionService

_FIXTURE = (Path(__file__).parent / "fixtures" / "last_funeral_of_god.md").read_bytes()
_n = {"v": 0}


def _msg(content, *, author="owner", attachment=None):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"pf{_n['v']}", guild_id="g", channel_id="chan-1",
        author_discord_id=author, author_display_name=author, content=content,
        attachments=(attachment,) if attachment else ())


async def _setup_black_chapel(db, provider):
    """Import + approve the extended fixture; place Veskan + Aria at Black Chapel
    with Mother Veyra / Father Caldus / Sister Nara present. Returns a dict of ids."""
    admin = AdminBridge(db, provider)
    await admin.handle(_msg("!rv campaign new The Last Funeral of God"))
    await admin.handle(_msg(
        "!rv campaign import",
        attachment=InboundAttachment("lfog.md", "text/markdown", _FIXTURE)))
    async with db.session() as s:
        draft = (await s.execute(select(CanonImport))).scalar_one()
    await admin.handle(_msg(f"!rv campaign import approve {draft.id}"))

    ids: dict = {}
    async with db.unit_of_work() as s:
        camp = CampaignService(s)
        campaign = await camp.resolve_campaign_by_channel("chan-1")
        campaign.config = {**campaign.config, "dice_mode": "AUTO"}
        mA = await camp.resolve_member(campaign.id, "owner")
        mB = await camp.add_member(campaign_id=campaign.id, discord_user_id="userB",
                                   display_name="Friend")
        chars = CharacterService(s)
        veskan = await chars.create_character(member_id=mA.id, name="Veskan", char_class="wizard")
        aria = await chars.create_character(member_id=mB.id, name="Aria", char_class="rogue")

        chapel = (await s.execute(select(Location).where(Location.name == "Black Chapel"))).scalar_one()
        veyra = (await s.execute(select(NPC).where(NPC.name == "Mother Veyra"))).scalar_one()
        caldus = (await s.execute(select(NPC).where(NPC.name == "Father Caldus"))).scalar_one()
        nara = (await s.execute(select(NPC).where(NPC.name == "Sister Nara"))).scalar_one()

        veskan.location_id = chapel.id
        aria.location_id = chapel.id

        sess = await SessionService(s).create_session(campaign_id=campaign.id, attendance=[mA.id, mB.id])
        await SessionService(s).start_session(sess.id)
        scene = await SceneService(s).create_scene(
            session_id=sess.id, location_id=chapel.id, mode=SceneMode.SOCIAL,
            purpose="เฝ้าโลงศพ",
            participants=[f"character:{veskan.id}", f"character:{aria.id}"],
            visible_entity_ids=[f"npc:{veyra.id}", f"npc:{caldus.id}", f"npc:{nara.id}"],
        )
        ids = {
            "campaign_id": campaign.id, "session_id": sess.id, "scene_id": scene.id,
            "veskan_id": veskan.id, "aria_id": aria.id,
            "veyra_id": veyra.id, "caldus_id": caldus.id, "nara_id": nara.id,
            "chapel_id": chapel.id,
        }
    return ids


def _protocol_echo(messages, _model) -> NPCResponse:
    """Stands in for a well-behaved model that reproduces a grounded protocol
    verbatim — proves the ENGINE delivered the ordered rules into the NPC's
    context; a real model's faithfulness is out of FakeLLM's scope."""
    blob = "\n".join(m.get("content", "") for m in messages)
    rules: list[str] = []
    capture = False
    for line in blob.splitlines():
        if "PROTOCOLS_KNOWN_TO_NPC" in line:
            capture = True
            continue
        if capture:
            m = re.match(r"\s*(\d+)\.\s+(.*)", line)
            if m:
                rules.append(m.group(2).strip())
            elif line.strip().startswith("LISTENER:"):
                break
    return NPCResponse(utterance="\n".join(f"{i + 1}. {r}" for i, r in enumerate(rules)))


_FIVE_RULES = [
    "คุ้มกันโลงศพ", "ห้ามเปิดโลง", "ห้ามให้ใครแตะต้องโลง",
    "หากนักบวชตาย ให้เดินทางต่อ", "หากโลงพูด ห้ามตอบ",
]


# --- TEST 1: exact five rules ------------------------------------------------

async def test_mother_veyra_recites_exact_five_rules_in_order(db, provider):
    ids = await _setup_black_chapel(db, provider)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ทวนกฎ", method="ถาม Mother Veyra", intent_confidence=0.9,
        target_references=["Mother Veyra"], social_intent=True))
    provider.on("generate_npc_response", _protocol_echo)
    dm_calls_before = len([c for c in provider.calls if c[0] == "generate_dm_narration"])

    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg(
        "! ผมถาม Mother Veyra ลูกอยากทวนเกี่ยวกับกฎ 5 ข้อ อีกครั้งท่านแม่ช่วยบอกลูกได้หรือไม่",
        author="owner"))

    body = r.responses[0].content
    for rule in _FIVE_RULES:
        assert rule in body
    # Order preserved, nothing invented.
    positions = [body.index(rule) for rule in _FIVE_RULES]
    assert positions == sorted(positions)
    assert "สัญลักษณ์" not in body and "หลบหนี" not in body
    assert r.responses[0].title == "Mother Veyra"
    # The generic narrator never ran for this turn — NPCSocialService answered directly.
    dm_calls_after = len([c for c in provider.calls if c[0] == "generate_dm_narration"])
    assert dm_calls_after == dm_calls_before


# --- TEST 2: Sister Nara never speaks aloud -----------------------------------

async def test_sister_nara_never_produces_spoken_dialogue(db, provider):
    ids = await _setup_black_chapel(db, provider)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ขอบคุณ", method="พูดกับ Sister Nara", intent_confidence=0.9,
        target_references=["Sister Nara"], social_intent=True))

    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมขอบคุณ Sister Nara", author="owner"))

    body = r.responses[0].content
    assert r.responses[0].title == "Sister Nara"
    assert "กระดานชนวน" in body            # rendered as a written/nonverbal act
    assert "พูดว่า" not in body             # never attributed spoken dialogue


# --- TEST 3: named NPC resolution among three present -------------------------

async def test_named_npc_resolution_targets_father_caldus_not_first_listed(db, provider):
    ids = await _setup_black_chapel(db, provider)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ถาม", method="พูดกับ Father Caldus", intent_confidence=0.9,
        target_references=["Father Caldus"], social_intent=True))

    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! Father Caldus ประตูโบสถ์ล็อกไหม?", author="owner"))

    assert r.responses[0].title == "Father Caldus"
    assert r.responses[0].title != "Mother Veyra"


# --- TEST 4: multi-NPC thanks --------------------------------------------------

async def test_multi_npc_thanks_resolves_all_present_targets(db, provider):
    ids = await _setup_black_chapel(db, provider)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ขอบคุณทุกคน", method="กล่าวขอบคุณ", intent_confidence=0.9,
        target_references=["Mother Veyra", "Father Caldus", "Sister Nara"], social_intent=True))

    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg(
        "! ผมขอบคุณ Mother Veyra, Father Caldus และ Sister Nara", author="owner"))

    titles = {resp.title for resp in r.responses}
    assert titles == {"Mother Veyra", "Father Caldus", "Sister Nara"}
    nara_resp = next(resp for resp in r.responses if resp.title == "Sister Nara")
    assert "กระดานชนวน" in nara_resp.content


# --- TEST 5: leave Black Chapel -> clean destination scene ---------------------

async def test_leaving_black_chapel_leaves_npcs_behind_in_a_clean_scene(db, provider):
    ids = await _setup_black_chapel(db, provider)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ออกไปข้างนอก", method="เดินออกประตูหน้า", intent_confidence=0.9,
        movement_intent=True, movement_kind="RETURN_OR_EXIT", movement_reference="ข้างนอก"))

    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมเดินออกไปข้างนอก", author="owner"))
    assert r.state_mutated
    assert r.responses[0].title == "Chapel Road"

    async with db.session() as s:
        new_scene = await SceneService(s).get_active_scene(ids["session_id"])
        assert new_scene.id != ids["scene_id"]
        assert new_scene.location_id != ids["chapel_id"]
        directory = await SceneEntityDirectory(s).build(
            new_scene, actor_character_id=ids["veskan_id"], campaign_id=ids["campaign_id"])
        present_names = {e.canonical_name for e in directory.present_npcs}
        assert present_names.isdisjoint({"Mother Veyra", "Father Caldus", "Sister Nara"})
        # They stayed behind — canonical position unchanged.
        veyra = await s.get(NPC, ids["veyra_id"])
        assert veyra.current_location_id == ids["chapel_id"]
    return ids, r


# --- TEST 6: return to the chapel later ---------------------------------------

async def test_returning_to_black_chapel_is_a_new_scene_not_a_replay(db, provider):
    ids = await _setup_black_chapel(db, provider)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ออกไปข้างนอก", method="เดินออกประตูหน้า", intent_confidence=0.9,
        movement_intent=True, movement_kind="RETURN_OR_EXIT", movement_reference="ข้างนอก"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    await bridge.handle_inbound(_msg("! ผมเดินออกไปข้างนอก", author="owner"))

    async with db.session() as s:
        street_scene = await SceneService(s).get_active_scene(ids["session_id"])
        street_scene_id = street_scene.id

    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="กลับเข้าไปในโบสถ์", method="เดินกลับ", intent_confidence=0.9,
        movement_intent=True, movement_kind="CANONICAL_TRAVEL", movement_reference="กลับไปในโบสถ์"))
    r = await bridge.handle_inbound(_msg("! ผมเดินกลับเข้าไปในโบสถ์", author="owner"))
    assert r.responses[0].title == "Black Chapel"

    async with db.session() as s:
        chapel_scene = await SceneService(s).get_active_scene(ids["session_id"])
        assert chapel_scene.id not in (ids["scene_id"], street_scene_id)  # a fresh scene
        directory = await SceneEntityDirectory(s).build(
            chapel_scene, actor_character_id=ids["veskan_id"], campaign_id=ids["campaign_id"])
        present_names = {e.canonical_name for e in directory.present_npcs}
        assert {"Mother Veyra", "Father Caldus", "Sister Nara"} <= present_names


# --- TEST 7: search for a smith -> one persistent ordinary shop ----------------

async def test_search_for_place_may_create_one_persistent_shop_without_chapel_npcs(db, provider):
    ids = await _setup_black_chapel(db, provider)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ออกไปข้างนอก", method="เดินออกประตูหน้า", intent_confidence=0.9,
        movement_intent=True, movement_kind="RETURN_OR_EXIT", movement_reference="ข้างนอก"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    await bridge.handle_inbound(_msg("! ผมเดินออกไปข้างนอก", author="owner"))

    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="หาช่างตีเหล็ก", method="เดินหาร้าน", intent_confidence=0.9,
        movement_intent=True, movement_kind="SEARCH_FOR_PLACE", movement_reference="ช่างตีเหล็กแถวนี้"))
    r = await bridge.handle_inbound(_msg("! ผมหาช่างตีเหล็กแถวนี้", author="owner"))
    assert r.state_mutated

    async with db.session() as s:
        shop = (await s.execute(select(Location).where(
            Location.provenance == "AI_EXPANDED"))).scalars().first()
        assert shop is not None
        shop_scene = await SceneService(s).get_active_scene(ids["session_id"])
        directory = await SceneEntityDirectory(s).build(
            shop_scene, actor_character_id=ids["veskan_id"], campaign_id=ids["campaign_id"])
        present_names = {e.canonical_name for e in directory.present_npcs}
        assert present_names.isdisjoint({"Mother Veyra", "Father Caldus", "Sister Nara"})
        return shop.id


# --- TEST 8: follow a vague sound creates no new Location ----------------------

async def test_follow_source_creates_no_new_location(db, provider):
    ids = await _setup_black_chapel(db, provider)
    async with db.session() as s:
        count_before = (await s.execute(select(func.count(Location.id)))).scalar_one()

    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ตามเสียงแว่วๆ", method="เดินตามเสียงอย่างระมัดระวัง", intent_confidence=0.7,
        movement_intent=True, movement_kind="FOLLOW_SOURCE",
        movement_reference="เสียงแว่วๆ"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    await bridge.handle_inbound(_msg(
        "! ผมตามเสียงแว่วๆ ไปอย่างระมัดระวัง โดยหลบสายตาของ Mother Veyra, Father Caldus "
        "และ Sister Nara", author="owner"))

    async with db.session() as s:
        count_after = (await s.execute(select(func.count(Location.id)))).scalar_one()
        assert count_after == count_before
        # Still the same scene/location — never routed into TravelService.
        scene = await SceneService(s).get_active_scene(ids["session_id"])
        assert scene.id == ids["scene_id"]


# --- TEST 9: leaving a generated shop returns via the canonical reverse edge ---

async def test_leaving_generated_shop_returns_via_canonical_reverse_edge(db, provider):
    ids = await _setup_black_chapel(db, provider)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ออกไปข้างนอก", method="เดินออกประตูหน้า", intent_confidence=0.9,
        movement_intent=True, movement_kind="RETURN_OR_EXIT", movement_reference="ข้างนอก"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    await bridge.handle_inbound(_msg("! ผมเดินออกไปข้างนอก", author="owner"))

    async with db.session() as s:
        street = await SceneService(s).get_active_scene(ids["session_id"])
        street_location_id = street.location_id

    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="หาช่างตีเหล็ก", method="เดินหาร้าน", intent_confidence=0.9,
        movement_intent=True, movement_kind="SEARCH_FOR_PLACE", movement_reference="ช่างตีเหล็กแถวนี้"))
    await bridge.handle_inbound(_msg("! ผมหาช่างตีเหล็กแถวนี้", author="owner"))

    async with db.session() as s:
        count_before = (await s.execute(select(func.count(Location.id)))).scalar_one()

    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ออกจากร้าน", method="เดินออกจากร้าน", intent_confidence=0.9,
        movement_intent=True, movement_kind="RETURN_OR_EXIT", movement_reference="ออกจากร้าน"))
    r = await bridge.handle_inbound(_msg("! ผมออกจากร้าน", author="owner"))

    async with db.session() as s:
        count_after = (await s.execute(select(func.count(Location.id)))).scalar_one()
        assert count_after == count_before          # no new valley/shop/forge
        scene = await SceneService(s).get_active_scene(ids["session_id"])
        assert scene.location_id == street_location_id


# --- TEST 10/11/12/13: rest routing --------------------------------------------

async def test_short_rest_routes_to_rest_service(db, provider):
    ids = await _setup_black_chapel(db, provider)
    async with db.unit_of_work() as s:
        campaign = await s.get(Campaign, ids["campaign_id"])
        before_time = campaign.current_game_time

    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="พักสั้น", method="นั่งพัก", intent_confidence=0.9,
        rest_intent=True, rest_kind="short", rest_scope="actor"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมพักสักหนึ่งชั่วโมง", author="owner"))
    assert "rest=short" in r.note

    async with db.session() as s:
        campaign = await s.get(Campaign, ids["campaign_id"])
        assert campaign.current_game_time == before_time + 60


async def test_long_rest_restores_hp_and_advances_480_minutes(db, provider):
    ids = await _setup_black_chapel(db, provider)
    async with db.unit_of_work() as s:
        veskan = await s.get(Character, ids["veskan_id"])
        veskan.hp = 1
        campaign = await s.get(Campaign, ids["campaign_id"])
        before_time = campaign.current_game_time

    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="พักยาว", method="นอนพัก", intent_confidence=0.9,
        rest_intent=True, rest_kind="long", rest_scope="actor"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมนอนพักคืนนี้", author="owner"))
    assert "rest=long completed" in r.note

    async with db.session() as s:
        veskan = await s.get(Character, ids["veskan_id"])
        assert veskan.hp == veskan.max_hp
        campaign = await s.get(Campaign, ids["campaign_id"])
        assert campaign.current_game_time == before_time + 480


async def test_ambiguous_rest_asks_one_clarification(db, provider):
    ids = await _setup_black_chapel(db, provider)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="พัก", method="พัก", intent_confidence=0.6,
        rest_intent=True, rest_kind="ambiguous"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมพัก", author="owner"))
    assert not r.state_mutated
    assert "พักสั้น" in r.responses[0].content and "พักยาว" in r.responses[0].content


async def test_interrupted_rest_withholds_benefits(db, provider):
    ids = await _setup_black_chapel(db, provider)
    async with db.unit_of_work() as s:
        veskan = await s.get(Character, ids["veskan_id"])
        veskan.hp = 1
        s.add(ScheduledWorldEvent(
            campaign_id=ids["campaign_id"], due_game_time=30, kind="bell_alarm",
            perceivable=True, payload={"summary": "ระฆังเตือนภัยดังขึ้นกะทันหัน"}))

    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="พักยาว", method="นอนพัก", intent_confidence=0.9,
        rest_intent=True, rest_kind="long", rest_scope="actor"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมนอนพักคืนนี้", author="owner"))
    assert "interrupted" in r.note
    assert "ระฆังเตือนภัย" in r.responses[0].content

    async with db.session() as s:
        veskan = await s.get(Character, ids["veskan_id"])
        assert veskan.hp == 1                    # no benefit — interrupted rest heals nothing


# --- TEST 14: PC rest agency (actor-only) --------------------------------------

async def test_party_rest_request_only_rests_the_actor(db, provider):
    ids = await _setup_black_chapel(db, provider)
    async with db.unit_of_work() as s:
        aria = await s.get(Character, ids["aria_id"])
        aria.hp = 1

    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="พวกเรานอนพัก", method="นอนพักทั้งปาร์ตี้", intent_confidence=0.9,
        rest_intent=True, rest_kind="long", rest_scope="party_request"))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    await bridge.handle_inbound(_msg("! พวกเรานอนพักคืนนี้", author="owner"))  # owner controls Veskan

    async with db.session() as s:
        aria = await s.get(Character, ids["aria_id"])
        assert aria.hp == 1                       # Aria's player never consented


# --- TEST 15: no secret leak through protocol/NPC grounding --------------------

async def test_protocol_grounding_does_not_leak_protected_secrets(db, provider):
    ids = await _setup_black_chapel(db, provider)
    from app.memory.context_builders import build_npc_response_context

    async with db.session() as s:
        veyra = await s.get(NPC, ids["veyra_id"])
        messages = await build_npc_response_context(
            s, npc=veyra, listener_ref=f"character:{ids['veskan_id']}", utterance="ทวนกฎห้าข้อ")
    blob = "\n".join(m["content"] for m in messages)
    assert "คุ้มกันโลงศพ" in blob
    assert "พระเจ้าไม่ได้ตาย" not in blob


# --- TEST 16: stale scene references are excluded ------------------------------

async def test_stale_scene_reference_excluded_from_directory(db, provider):
    ids = await _setup_black_chapel(db, provider)
    async with db.unit_of_work() as s:
        # Move Sister Nara elsewhere without updating the (still-Black-Chapel) scene.
        street = (await s.execute(select(Location).where(
            Location.name == "Chapel Road"))).scalar_one()
        nara = await s.get(NPC, ids["nara_id"])
        nara.current_location_id = street.id

    async with db.session() as s:
        scene = await s.get(Scene, ids["scene_id"])
        directory = await SceneEntityDirectory(s).build(
            scene, actor_character_id=ids["veskan_id"], campaign_id=ids["campaign_id"])
        present_names = {e.canonical_name for e in directory.present_npcs}
        assert "Sister Nara" not in present_names
        assert {"Mother Veyra", "Father Caldus"} <= present_names
