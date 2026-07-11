"""P0 multiplayer identity + entity-resolution regression suite.

Two distinct Discord users each control a distinct player character. These tests
pin: actor mapping, party-character resolution (not NPC invention), Thai aliases,
PC agency, presence != party membership, NPC target order, ambiguity, Discord-name
namespace separation, actor/target non-swap, party↔scene consistency, dialogue
speaker identity, and no PLAYER_ONLY leakage into another player's action context.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import InboundMessage
from app.engine import build_bridge
from app.entities import SceneEntityDirectory
from app.models.campaign import Campaign
from app.models.character import Character
from app.models.enums import (
    DifficultyBand,
    EventType,
    MemberRole,
    ResolutionType,
    SceneMode,
)
from app.models.event import Event
from app.models.scene import Scene
from app.schemas.llm_io import ActionInterpretation, AdjudicationDecision
from app.services.campaigns import CampaignService, CharacterService
from app.services.scenes import SceneService
from app.services.sessions import SessionService

_n = {"v": 0}


@dataclass
class TwoPC:
    campaign_id: str
    session_id: str
    scene_id: str
    veskan_id: str
    aria_id: str
    memberA_id: str
    memberB_id: str


async def setup_two_pc(db, *, aria_present=True, npc_names=(), dice_mode="AUTO") -> TwoPC:
    async with db.unit_of_work() as s:
        camp = CampaignService(s)
        chars = CharacterService(s)
        campaign = await camp.create_campaign(
            name="โต๊ะสองคน", discord_guild_id="g", game_channel_id="chan-1",
            owner_discord_user_id="userA", owner_display_name="Ashley",
        )
        await camp.activate_campaign(campaign.id)
        campaign.config = {**campaign.config, "dice_mode": dice_mode}
        mA = await camp.resolve_member(campaign.id, "userA")
        mB = await camp.add_member(campaign_id=campaign.id, discord_user_id="userB",
                                   display_name="Friend", role=MemberRole.PLAYER)
        veskan = await chars.create_character(member_id=mA.id, name="Veskan",
                                              char_class="wizard", abilities={"int": 16})
        aria = await chars.create_character(member_id=mB.id, name="Aria",
                                            char_class="rogue", abilities={"dex": 16})
        aria.aliases = ["อาเรีย"]
        veskan.aliases = ["เวสกัน"]

        npcs = []
        from app.npcs import NPCService
        for name in npc_names:
            npc = await NPCService(s).create_npc(campaign_id=campaign.id, name=name)
            npcs.append(npc.id)

        sess = await SessionService(s).create_session(campaign_id=campaign.id)
        await SessionService(s).start_session(sess.id)
        participants = [f"character:{veskan.id}"]
        if aria_present:
            participants.append(f"character:{aria.id}")
        scene = await SceneService(s).create_scene(
            session_id=sess.id, location_id=None, mode=SceneMode.SOCIAL,
            participants=participants,
            visible_entity_ids=[f"npc:{nid}" for nid in npcs],
        )
        return TwoPC(campaign.id, sess.id, scene.id, veskan.id, aria.id, mA.id, mB.id)


def _msg(content, author):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"mp{_n['v']}", guild_id="g", channel_id="chan-1",
        author_discord_id=author, author_display_name="?", content=content,
    )


def _interp(provider, *, targets, commands_other_pc=False):
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ทำอะไรบางอย่าง", method="เข้าไปหา", target_references=list(targets),
        intent_confidence=0.9, missing_information=[],
        commands_other_pc=commands_other_pc,
    ))


def _auto(provider):
    """Adjudicate to an automatic success so tests aren't about the roll."""
    provider.on("adjudicate_uncertain_action", lambda m, model: AdjudicationDecision(
        resolution_type=ResolutionType.AUTOMATIC_SUCCESS))


async def _committed_event(db, campaign_id) -> Event:
    async with db.session() as s:
        return (await s.execute(
            select(Event).where(Event.event_type == EventType.PLAYER_ACTION_COMMITTED.value)
            .order_by(Event.seq.desc()))).scalars().first()


# --- TEST 1: two-player actor mapping ------------------------------------------------

async def test_actor_mapping_is_per_sender(db, provider):
    w = await setup_two_pc(db)
    _auto(provider)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))

    _interp(provider, targets=["Aria"])
    r = await bridge.handle_inbound(_msg("! ผมเดินไปหา Aria", "userA"))
    ev = await _committed_event(db, w.campaign_id)
    assert ev.actor_entity == f"character:{w.veskan_id}"
    assert ev.payload["targets"][0]["ref"] == f"character:{w.aria_id}"

    _interp(provider, targets=["Veskan"])
    await bridge.handle_inbound(_msg("! ผมเดินไปหา Veskan", "userB"))
    ev = await _committed_event(db, w.campaign_id)
    assert ev.actor_entity == f"character:{w.aria_id}"
    assert ev.payload["targets"][0]["ref"] == f"character:{w.veskan_id}"


# --- TEST 2: existing party character, not a new NPC ---------------------------------

async def test_party_name_resolves_to_existing_pc(db, provider):
    w = await setup_two_pc(db)
    _auto(provider)
    _interp(provider, targets=["Aria"])
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    await bridge.handle_inbound(_msg("! ผมถาม Aria ว่าเธอเจออะไร", "userA"))
    ev = await _committed_event(db, w.campaign_id)
    t = ev.payload["targets"][0]
    assert t["ref"] == f"character:{w.aria_id}" and t["type"] == "PLAYER_CHARACTER"
    # No NPC was invented.
    from app.models.npc import NPC
    async with db.session() as s:
        assert (await s.execute(select(NPC))).scalars().all() == []


# --- TEST 3: Thai alias ---------------------------------------------------------------

async def test_thai_alias_resolves(db, provider):
    w = await setup_two_pc(db)
    _auto(provider)
    _interp(provider, targets=["อาเรีย"])
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    await bridge.handle_inbound(_msg("! ผมถามอาเรียว่าเธอเห็นอะไร", "userA"))
    ev = await _committed_event(db, w.campaign_id)
    assert ev.payload["targets"][0]["ref"] == f"character:{w.aria_id}"


# --- TEST 4 & 5: another player's agency ---------------------------------------------

async def test_command_over_other_pc_is_refused(db, provider):
    w = await setup_two_pc(db)
    _interp(provider, targets=["Aria"], commands_other_pc=True)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมบอก Aria ให้ตามผมมา", "userA"))
    assert r.state_mutated is False
    assert "pc-agency" in r.note
    assert "Aria" in r.responses[0].content
    # No committed action event mutated Aria.
    async with db.session() as s:
        acts = (await s.execute(select(Event).where(
            Event.event_type == EventType.PLAYER_ACTION_COMMITTED.value))).scalars().all()
        assert acts == []


async def test_declaring_other_pc_action_is_refused(db, provider):
    w = await setup_two_pc(db)
    _interp(provider, targets=["Aria"], commands_other_pc=True)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! Aria เดินไปเปิดประตู", "userA"))
    assert r.state_mutated is False and "pc-agency" in r.note


# --- TEST 6: physical action on an incapacitated PC is allowed -----------------------

async def test_physical_action_on_unconscious_pc_resolves(db, provider):
    w = await setup_two_pc(db)
    async with db.unit_of_work() as s:
        aria = await s.get(Character, w.aria_id)
        aria.hp = 0                               # unconscious -> cannot choose
    _auto(provider)
    # Dragging an ally who can't choose is NOT commanding a voluntary action.
    _interp(provider, targets=["Aria"], commands_other_pc=False)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมลาก Aria ออกจากกองไฟ", "userA"))
    assert r.state_mutated is True                # adjudicated, not blocked
    ev = await _committed_event(db, w.campaign_id)
    assert ev.payload["targets"][0]["ref"] == f"character:{w.aria_id}"


# --- TEST 7: NPC target by name, not list order --------------------------------------

async def test_npc_target_resolves_by_name_not_order(db, provider):
    w = await setup_two_pc(db, npc_names=["ทหารยาม", "พ่อค้า", "นักบวชลัทธิ"])
    _auto(provider)
    _interp(provider, targets=["พ่อค้า"])         # Merchant is second in the list
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    await bridge.handle_inbound(_msg("! ผมขู่พ่อค้า", "userA"))
    ev = await _committed_event(db, w.campaign_id)
    t = ev.payload["targets"][0]
    assert t["name"] == "พ่อค้า" and t["type"] == "NPC"
    assert "ทหารยาม" not in t["name"]             # never the first-listed guard


# --- TEST 8: same-name ambiguity -> one clarification --------------------------------

async def test_same_name_ambiguity_asks_once(db, provider):
    w = await setup_two_pc(db, npc_names=["Ren", "Ren"])
    _interp(provider, targets=["Ren"])
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมเรียก Ren", "userA"))
    assert r.state_mutated is False
    assert "Ren" in r.responses[0].content         # a focused disambiguation
    assert "คนไหน" in r.responses[0].content


# --- TEST 9: party member not physically present -------------------------------------

async def test_absent_party_member_is_not_reachable(db, provider):
    w = await setup_two_pc(db, aria_present=False)  # Aria split off
    _auto(provider)
    _interp(provider, targets=["Aria"])
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมยื่นหนังสือให้ Aria", "userA"))
    assert r.state_mutated is False
    assert "ไม่ได้อยู่ในฉากนี้" in r.responses[0].content
    # Nothing committed.
    assert await _committed_event(db, w.campaign_id) is None


# --- TEST 10: Discord display name is not a character alias ---------------------------

async def test_discord_name_is_not_a_character_alias(db, provider):
    w = await setup_two_pc(db)                     # userB's display name is "Friend"
    _auto(provider)
    _interp(provider, targets=["Friend"])          # the Discord name, not "Aria"
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("! ผมเดินไปหา Friend", "userA"))
    ev = await _committed_event(db, w.campaign_id)
    # "Friend" does not resolve to Aria — no PC target recorded.
    refs = [t["ref"] for t in (ev.payload.get("targets") or [])]
    assert f"character:{w.aria_id}" not in refs


# --- TEST 11: narrator receives actor/target without swapping ------------------------

async def test_directory_actor_and_targets_are_distinct(db, provider):
    w = await setup_two_pc(db)
    async with db.session() as s:
        scene = await s.get(Scene, w.scene_id)
        directory = await SceneEntityDirectory(s).build(
            scene, actor_character_id=w.aria_id, campaign_id=w.campaign_id)
    assert directory.actor.entity_ref == f"character:{w.aria_id}"
    assert directory.actor.is_actor
    others = [e.canonical_name for e in directory.present_player_characters if not e.is_actor]
    assert others == ["Veskan"]
    resolution = directory.resolve_mentions(["Veskan"])
    assert resolution.primary.entity_ref == f"character:{w.veskan_id}"
    assert resolution.primary.entity_ref != directory.actor.entity_ref


# --- TEST 12: party view == scene directory (one truth) ------------------------------

async def test_party_view_and_directory_are_consistent(db, provider):
    w = await setup_two_pc(db)
    async with db.session() as s:
        scene = await s.get(Scene, w.scene_id)
        directory = await SceneEntityDirectory(s).build(
            scene, actor_character_id=w.veskan_id, campaign_id=w.campaign_id)
        present = {e.canonical_name for e in directory.present_player_characters}
        members = await CampaignService(s).list_members(w.campaign_id)
        party = set()
        for m in members:
            c = await CharacterService(s).get_active_character(m)
            if c is not None:
                party.add(c.name)
    assert present == party == {"Veskan", "Aria"}


# --- TEST 13: character dialogue keeps speaker identity ------------------------------

async def test_character_dialogue_preserves_speaker(db, provider):
    from app.models.enums import MessageCategory
    from app.orchestration.router import MessageRouter

    w = await setup_two_pc(db)
    provider.push("classify_table_message",
                  __import__("app.schemas.llm_io", fromlist=["ClassificationResult"])
                  .ClassificationResult(category=MessageCategory.CHARACTER_DIALOGUE, confidence=0.9))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_msg("Veskan เจ้าเห็นเหมือนที่ข้าเห็นไหม?", "userB"))
    assert r.state_mutated is False
    assert "speaker=Aria" in r.note                # Aria's line stays Aria's


# --- TEST 14: no PLAYER_ONLY leak into another player's action context ---------------

async def test_action_context_excludes_other_pc_private_data(db, provider):
    from app.memory.context_builders import build_action_interpretation_context

    w = await setup_two_pc(db)
    async with db.unit_of_work() as s:
        aria = await s.get(Character, w.aria_id)
        aria.hooks = {"secret": "SECRET_อาเรียเป็นสายลับ"}
        aria.appearance = "PRIVATE_รอยสักลับ"
    async with db.session() as s:
        scene = await s.get(Scene, w.scene_id)
        veskan = await s.get(Character, w.veskan_id)
        directory = await SceneEntityDirectory(s).build(
            scene, actor_character_id=w.veskan_id, campaign_id=w.campaign_id)
        messages = await build_action_interpretation_context(
            s, action_text="ผมเดินไปหา Aria", scene=scene, character=veskan,
            directory=directory)
    blob = "\n".join(m["content"] for m in messages)
    assert "Aria" in blob                          # enough to resolve her
    assert "SECRET_" not in blob                   # but not her private hooks
    assert "PRIVATE_" not in blob                  # nor her appearance
