"""Scene-packet continuity (issue #1 — companions and a living environment).

Two required scenarios that exercise the SCENE PACKET the narrator is handed:
  6. Two companions present in one scene both reach the packet, and companions with
     different state behave differently (they are not interchangeable background).
  7. An environmental hazard sourced from persistent location state evolves across
     turns and reaches the packet — the narrator never resets the world each turn.
"""
from __future__ import annotations

from app.memory.context_builders import build_narration_context
from app.memory.scene_context import SceneContextBuilder
from app.models.location import Location
from app.models.npc import NPC
from app.npcs.decision_service import NPCDecisionService
from app.npcs.memory_service import NPCMemoryService
from app.npcs.npc_service import NPCService
from app.entities import SceneEntityDirectory
from app.models.scene import Scene
from app.services.scenes import SceneService
from tests.support.factories import build_world, start_session_with_scene


# --- scenario 6: two companions in one scene ---------------------------------

async def test_two_present_companions_both_reach_the_narration_packet(db, provider):
    world = await build_world(db)          # guard NPC already at the opening location
    sid, scene_id = await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        odette = await NPCService(s).create_npc(
            campaign_id=world.campaign_id, name="โอเด็ตต์นักปราชญ์",
            personality="ระมัดระวัง", current_location_id=world.location_id)
        scene = await s.get(Scene, scene_id)
        scene.visible_entity_ids = list(scene.visible_entity_ids or []) + [f"npc:{odette.id}"]

    async with db.session() as read:
        scene = await SceneService(read).get_active_scene(sid)
        scene_ctx = await SceneContextBuilder(read).build(
            campaign_id=world.campaign_id, scene=scene, actor_character_id=world.kael_id)
        directory = await SceneEntityDirectory(read).build(
            scene, actor_character_id=world.kael_id, campaign_id=world.campaign_id)
        messages = await build_narration_context(
            read, action_text="ผมกวาดสายตามองทั้งห้อง", outcome="success",
            result_summary="มองเห็นทุกคนในห้อง", scene=scene,
            directory=directory, scene_context=scene_ctx)

    blob = "\n".join(m["content"] for m in messages)
    # Both companions are in the packet, so the narrator can have BOTH act this turn.
    assert "ยามเฝ้าประตู" in blob
    assert "โอเด็ตต์นักปราชญ์" in blob
    assert scene_ctx.present_npcs.count("โอเด็ตต์นักปราชญ์") == 1


async def test_companions_with_different_state_derive_different_behaviour(db, provider):
    """An emergent difference, not a scripted one: the same prompt yields different
    autonomous follow-ups because the two NPCs are in different states."""
    world = await build_world(db)
    listener = f"character:{world.kael_id}"
    async with db.unit_of_work() as s:
        cautious = await NPCService(s).create_npc(
            campaign_id=world.campaign_id, name="นักปราชญ์ขี้กังวล",
            personality="ระมัดระวัง", current_location_id=world.location_id)
        wary = await NPCService(s).create_npc(
            campaign_id=world.campaign_id, name="ยามหูไว",
            personality="ระแวง", current_location_id=world.location_id)
        # Only the wary one has reason to be suspicious of this character.
        rel = await NPCMemoryService(s)._relationship(wary.id, listener)
        rel.suspicion = 85
        rel.familiarity = 10

    async with db.session() as s:
        wary_decision = await NPCDecisionService(s).decide(
            npc=await s.get(NPC, wary.id), listener_ref=listener, utterance="ขอเดินผ่านหน่อย")
        calm_decision = await NPCDecisionService(s).decide(
            npc=await s.get(NPC, cautious.id), listener_ref=listener, utterance="ขอเดินผ่านหน่อย")

    # The suspicious guard acts on its state; the neutral scholar does not — same scene,
    # different behaviour, driven by persistent NPC state rather than list order.
    assert wary_decision.followups
    assert wary_decision.followups != calm_decision.followups


# --- scenario 7: an environmental hazard that evolves across turns ------------

async def test_environmental_hazard_evolves_across_turns_in_the_packet(db, provider):
    world = await build_world(db)
    sid, _ = await start_session_with_scene(db, world)
    async with db.session() as read:
        loc_id = (await SceneService(read).get_active_scene(sid)).location_id

    async def _location_block() -> str:
        async with db.session() as read:
            scene = await SceneService(read).get_active_scene(sid)
            ctx = await SceneContextBuilder(read).build(
                campaign_id=world.campaign_id, scene=scene,
                actor_character_id=world.kael_id)
            return ctx.location_block()

    # Turn 1: a fire starts. It is canonical location state, not narrator invention.
    async with db.unit_of_work() as s:
        (await s.get(Location, loc_id)).current_activity = "ไฟเริ่มลามจากกองฟางที่มุมห้อง"
    block1 = await _location_block()
    assert "ไฟเริ่มลามจากกองฟาง" in block1

    # Turn 2: the fire spreads. The environment carried forward and CHANGED — the packet
    # reflects the new state, and the old line is not stuck on repeat.
    async with db.unit_of_work() as s:
        (await s.get(Location, loc_id)).current_activity = "ไฟลามทั่วห้อง ควันหนาจนแสบตา"
    block2 = await _location_block()
    assert "ไฟลามทั่วห้อง" in block2
    assert "ไฟเริ่มลามจากกองฟาง" not in block2
