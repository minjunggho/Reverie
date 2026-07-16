"""Narrated entities persist (Critical Failure 7): no prose ghosts.

A narration that introduces a person/creature must declare it, and the ENGINE
commits a real NPC — at the scene's canonical location, listed in the scene's
visible entities — in the same transaction that marks the response sent. The next
turn can approach/talk to her; a retry or re-mention never spawns a twin.
"""
from __future__ import annotations

from sqlalchemy import select

from app.discord_bridge import InboundMessage
from app.engine import build_bridge
from app.entities.directory import SceneEntityDirectory
from app.models.character import Character
from app.models.npc import NPC
from app.models.scene import Scene
from app.schemas.llm_io import IntroducedNPC, Narration
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"ne-{_n['v']}", guild_id="guild-1", channel_id="chan-1",
        author_discord_id="disc-p1", author_display_name="กี้", content=content)


def _introducing_narration(messages, _model) -> Narration:
    return Narration(
        text="ที่มุมห้อง หญิงติดเชื้อคนหนึ่งทรุดตัวพิงผนัง ไอแห้งๆ ไม่หยุด",
        decision_prompt="Kael จะทำอย่างไร?",
        introduced_npcs=[IntroducedNPC(name="หญิงติดเชื้อ",
                                       descriptor="ผิวซีด ไอไม่หยุด ดวงตาขุ่น")])


async def _setup(db, provider):
    world = await build_world(db)
    _, scene_id = await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        for cid in (world.kael_id, world.bront_id):
            (await s.get(Character, cid)).location_id = world.location_id
    provider.on("generate_dm_narration", _introducing_narration)
    return world, scene_id, build_bridge(db, provider=provider)


async def test_introduced_npc_is_committed_before_delivery(db, provider):
    world, scene_id, bridge = await _setup(db, provider)

    await bridge.handle_inbound(_msg("! ผมตรวจดูรอยเลือดบนพื้นอย่างละเอียด"))

    async with db.session() as s:
        npc = (await s.execute(select(NPC).where(
            NPC.campaign_id == world.campaign_id,
            NPC.name == "หญิงติดเชื้อ"))).scalar_one()
        assert npc.current_location_id == world.location_id   # real, placed entity
        scene = await s.get(Scene, scene_id)
        assert f"npc:{npc.id}" in (scene.visible_entity_ids or [])

        # Next turn she is resolvable — approaching her can never loop on
        # "which direction?".
        directory = await SceneEntityDirectory(s).build(
            scene, actor_character_id=world.kael_id, campaign_id=world.campaign_id)
        resolution = directory.resolve_mentions(["หญิงติดเชื้อ"])
        assert resolution.resolved and not resolution.unresolved


async def test_reintroduction_never_spawns_a_twin(db, provider):
    world, _scene_id, bridge = await _setup(db, provider)

    await bridge.handle_inbound(_msg("! ผมตรวจดูรอยเลือดบนพื้น"))
    await bridge.handle_inbound(_msg("! ผมมองไปรอบๆ อีกครั้ง"))   # same narration again

    async with db.session() as s:
        rows = (await s.execute(select(NPC).where(
            NPC.campaign_id == world.campaign_id,
            NPC.name == "หญิงติดเชื้อ"))).scalars().all()
    assert len(rows) == 1                                      # deduplicated
