"""Player-intent: ordered compound actions, future-intention safety, following.

Drives the production bridge (build_bridge → handle_inbound). The interpreter
splits compound Thai into typed ordered steps; the pipeline executes them IN ORDER
through the same single-action routing every simple action uses, committing between
steps so an earlier consequence can prevent a later one. Dialogue / future
intention is preserved but never executed as a physical action (no teleport).
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import AdminBridge, InboundMessage, is_admin_command
from app.engine import build_bridge
from app.models.character import Character
from app.presentation import MessageKind
from app.world import LocationService, PositionService
from app.world.graph_service import WorldGraphService
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1", name="กี้"):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"ap{_n['v']}", guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name=name, content=content,
    )


class Table:
    def __init__(self, db, provider, rng=None):
        self.game = build_bridge(db, provider=provider, rng=rng or SequenceRandomness(default=12))
        self.admin = AdminBridge(db, provider, creation_flow=self.game.creation_flow,
                                 session_zero=self.game.session_zero)

    async def send(self, content, author="disc-p1", name="กี้"):
        inbound = _msg(content, author=author, name=name)
        if is_admin_command(content):
            return await self.admin.handle(inbound)
        return await self.game.handle_inbound(inbound)


async def _char(db, cid):
    async with db.session() as s:
        return await s.get(Character, cid)


async def _two_room_scene(db):
    """A hall→yard graph with both PCs + the guard NPC present in the hall."""
    world = await build_world(db)
    async with db.unit_of_work() as s:
        yard = await LocationService(s).create_location(
            campaign_id=world.campaign_id, name="ลานหน้า", description_obvious="ลานโล่ง")
        await WorldGraphService(s).add_connection(
            campaign_id=world.campaign_id, from_location_id=world.location_id,
            to_location_id=yard.id, label="ออกไปข้างนอก", travel_minutes=5)
        for cid in (world.kael_id, world.bront_id):
            c = await s.get(Character, cid)
            c.location_id = world.location_id
        yard_id = yard.id
    sid, _ = await start_session_with_scene(db, world)
    return world, sid, world.location_id, yard_id


# --- required example 1: dialogue + future intention must NOT teleport ----------

async def test_thanks_with_future_errand_does_not_move_the_player(db, provider):
    world, sid, hall, yard = await _two_room_scene(db)
    table = Table(db, provider)
    before = (await _char(db, world.kael_id)).location_id
    # "Thank you, but I'll need to go run an errand later" — dialogue + FUTURE intent.
    r = await table.send("! ขอบคุณค่ะ แต่เดี๋ยวหนูต้องไปทำธุระ")
    after = (await _char(db, world.kael_id)).location_id
    assert after == before == hall                           # NOT teleported
    # It was treated as speech/intent, not movement — no scene-frame travel result.
    assert all(m.kind != MessageKind.SCENE_FRAME or "ลานหน้า" not in (m.content or "")
               for m in r.responses)


# --- required example 2: speak → leave building → route toward the guard --------

async def test_thank_then_leave_then_head_to_guard_executes_in_order(db, provider):
    world, sid, hall, yard = await _two_room_scene(db)
    # Put a "ยาม" (guard) target out in the yard so "ไปหายาม" routes there.
    async with db.unit_of_work() as s:
        from app.npcs import NPCService
        guard = await NPCService(s).create_npc(
            campaign_id=world.campaign_id, name="ยามประตู", current_location_id=yard)
    table = Table(db, provider)
    r = await table.send("! ขอบคุณเขา แล้วเดินออกจากร้านไปหายาม")
    # The actor actually left the hall (the MOVE step committed).
    assert (await _char(db, world.kael_id)).location_id == yard
    assert r.state_mutated
    assert "steps" in r.note                                 # ran as an ordered plan


# --- required example 3: attack → grab → flee, earlier consequence may halt -----

async def test_attack_grab_flee_runs_in_order(db, provider):
    world, sid, hall, yard = await _two_room_scene(db)
    async with db.unit_of_work() as s:
        from app.npcs import NPCService
        await NPCService(s).create_npc(
            campaign_id=world.campaign_id, name="บาร์เทนเดอร์", current_location_id=hall)
    table = Table(db, provider, rng=SequenceRandomness(default=15))
    r = await table.send("! ต่อยบาร์เทนเดอร์ หยิบจดหมาย แล้ววิ่งหนี")
    # Three ordered steps attempted through the one pipeline; state changed.
    assert r.state_mutated and "ordered plan" in r.note
    assert len(r.responses) >= 2                              # per-step outcomes


async def test_ordered_plan_halts_when_a_physical_step_is_blocked(db, provider):
    """If a MOVE step can't happen (no such exit), the chain stops and the
    remaining steps are reported as not attempted — earlier consequence prevents
    the rest."""
    world, sid, hall, yard = await _two_room_scene(db)
    table = Table(db, provider, rng=SequenceRandomness(default=15))
    # "search the shelf, then go to the moon, then dance" — step 2 has no route.
    from app.schemas.llm_io import ActionInterpretation, ActionStep
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="หลายขั้น", method="", intent_confidence=0.9, steps=[
            ActionStep(kind="SEARCH", text="ค้นชั้นวาง", method="ค้นหา"),
            ActionStep(kind="MOVE", text="ไปดวงจันทร์", destination="ดวงจันทร์"),
            ActionStep(kind="SPEAK", text="เต้นรำ"),
        ]))
    r = await table.send("! ค้นชั้นวาง แล้วไปดวงจันทร์ แล้วเต้นรำ")
    assert "halted at MOVE" in r.note
    assert any("ถูกยกเลิก" in (m.content or "") for m in r.responses)   # remaining cancelled


# --- natural following (reuses the consent system) ------------------------------

async def test_natural_language_follow_and_stop(db, provider):
    world, sid, hall, yard = await _two_room_scene(db)
    table = Table(db, provider)
    r = await table.send("! ฉันตาม Bront ไป", author=world.p1_discord_id)
    assert "จะเดินทางตาม" in r.responses[0].content
    assert (await _char(db, world.kael_id)).following_character_id == world.bront_id
    # "I stay here" clears it.
    r = await table.send("! ฉันอยู่ที่นี่", author=world.p1_discord_id)
    assert (await _char(db, world.kael_id)).following_character_id is None


async def test_follow_requires_co_location(db, provider):
    world, sid, hall, yard = await _two_room_scene(db)
    async with db.unit_of_work() as s:      # move Bront away first
        bront = await s.get(Character, world.bront_id)
        bront.location_id = yard
    table = Table(db, provider)
    r = await table.send("! ฉันตาม Bront ไป", author=world.p1_discord_id)
    assert "ต้องอยู่ที่เดียวกับ" in r.responses[0].content
    assert (await _char(db, world.kael_id)).following_character_id is None


async def test_english_follow_phrase_also_works(db, provider):
    world, sid, hall, yard = await _two_room_scene(db)
    table = Table(db, provider)
    r = await table.send("! I follow Bront", author=world.p1_discord_id)
    assert (await _char(db, world.kael_id)).following_character_id == world.bront_id


# --- the planner unit: temporal filtering + single-step fallback ---------------

def test_build_plan_excludes_future_and_flavor_steps():
    from app.orchestration.action_plan import build_plan
    from app.schemas.llm_io import ActionInterpretation, ActionStep

    interp = ActionInterpretation(
        goal="x", method="y", intent_confidence=0.9, steps=[
            ActionStep(kind="SPEAK", text="ขอบคุณ", temporal="IMMEDIATE"),
            ActionStep(kind="MOVE", text="เดี๋ยวจะไป", temporal="FUTURE"),
            ActionStep(kind="OTHER", text="มองไปรอบๆ", temporal="FLAVOR"),
        ])
    plan = build_plan(interp)
    assert [s.kind for s in plan.executable_steps] == ["SPEAK"]   # only IMMEDIATE


def test_build_plan_single_step_fallback_from_flat_interpretation():
    from app.orchestration.action_plan import build_plan
    from app.schemas.llm_io import ActionInterpretation

    interp = ActionInterpretation(goal="ร่ายไฟ", method="ร่าย", intent_confidence=0.9,
                                  cast_intent=True, spell_reference="fire_bolt")
    plan = build_plan(interp)
    assert len(plan.steps) == 1 and plan.steps[0].kind == "CAST"
