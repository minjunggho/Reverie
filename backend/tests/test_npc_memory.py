"""§7/§10 — NPC episodic memory + player-specific relationships, end to end.

The loop under test: a committed player→NPC interaction creates a typed NPCMemory
linked to its source event and accumulates multi-dimensional relationship state;
retrieval scopes both to ONE npc + ONE listener; the NPC's later prompt carries
them, so behaviour is player-specific and survives across sessions (a fresh DB
session = a process restart against the same store).
"""
from __future__ import annotations

from sqlalchemy import select

from app.memory.context_builders import build_npc_response_context
from app.models.npc import NPC
from app.models.npc_epistemic import NPCMemory, NPCRelationship
from app.npcs import NPCSocialService
from app.npcs.memory_service import NPCMemoryService, classify_interaction
from tests.support.factories import build_world


# --- classification ------------------------------------------------------------

def test_classification_distinguishes_major_interactions():
    assert classify_interaction("ข้าจะฆ่าเจ้าถ้าเจ้าโกหก").memory_type == "ASSAULT"
    assert classify_interaction("ถ้าไม่บอกความจริง เจ้าจะเสียใจ").memory_type == "THREAT"
    assert classify_interaction("ขอบคุณมากที่ช่วยข้า").memory_type == "HELP"  # 'ช่วย' wins
    assert classify_interaction("ขอบคุณนะ").memory_type == "AFFECTION"
    assert classify_interaction("สวัสดีตอนเช้า").memory_type == "INTERACTION"


# --- write + accumulate --------------------------------------------------------

async def test_threat_creates_memory_and_raises_fear(db, provider):
    world = await build_world(db)
    listener = f"character:{world.kael_id}"
    async with db.unit_of_work() as s:
        await NPCMemoryService(s).record_interaction(
            npc_id=world.guard_npc_id, listener_ref=listener, listener_name="Kael",
            utterance="ถ้าเจ้าไม่เปิดประตู ข้าจะขู่ให้เจ้าเสียใจ", game_time=100)
    async with db.session() as s:
        mem = (await s.execute(select(NPCMemory).where(
            NPCMemory.npc_id == world.guard_npc_id))).scalar_one()
        assert mem.memory_type == "THREAT"
        assert mem.subject_ref == listener
        assert mem.emotional_valence < 0
        rel = (await s.execute(select(NPCRelationship).where(
            NPCRelationship.npc_id == world.guard_npc_id))).scalar_one()
        assert rel.fear > 0 and rel.trust < 0
        assert rel.current_stance in ("afraid", "guarded", "hostile", "wary")


async def test_repeated_pressure_escalates_reaction(db, provider):
    """Repeated requests/threats accumulate — anger/fear keep climbing, not reset."""
    world = await build_world(db)
    listener = f"character:{world.kael_id}"
    async with db.unit_of_work() as s:
        svc = NPCMemoryService(s)
        for i in range(3):
            await svc.record_interaction(
                npc_id=world.guard_npc_id, listener_ref=listener, listener_name="Kael",
                utterance="ข้าขู่เจ้าอีกครั้ง เปิดประตูเดี๋ยวนี้", game_time=100 + i)
    async with db.session() as s:
        rel = (await s.execute(select(NPCRelationship).where(
            NPCRelationship.npc_id == world.guard_npc_id))).scalar_one()
        assert rel.fear >= 60          # 3 × +25 (clamped at 100)
        mems = (await s.execute(select(NPCMemory).where(
            NPCMemory.npc_id == world.guard_npc_id))).scalars().all()
        assert len(mems) == 3          # each pressure is its own episodic memory


# --- player-specific: different characters, different feeling ------------------

async def test_memory_and_relationship_are_scoped_per_character(db, provider):
    """The threatening character is feared; the helpful one is trusted — the same
    NPC holds a DIFFERENT relationship toward each party member."""
    world = await build_world(db)
    threatener = f"character:{world.kael_id}"
    helper = f"character:{world.bront_id}"
    async with db.unit_of_work() as s:
        svc = NPCMemoryService(s)
        await svc.record_interaction(npc_id=world.guard_npc_id, listener_ref=threatener,
                                     listener_name="Kael", utterance="ข้าจะขู่เจ้า")
        await svc.record_interaction(npc_id=world.guard_npc_id, listener_ref=helper,
                                     listener_name="Bront", utterance="ข้าจะช่วยรักษาเจ้า")
    async with db.session() as s:
        svc = NPCMemoryService(s)
        r_threat = await svc.recall(npc_id=world.guard_npc_id, listener_ref=threatener)
        r_help = await svc.recall(npc_id=world.guard_npc_id, listener_ref=helper)
        assert r_threat.relationship.fear > 0
        assert r_help.relationship.trust > 0 and r_help.relationship.fear == 0
        # Retrieval never bleeds one character's memories into the other's recall.
        assert all(m.subject_ref == threatener for m in r_threat.memories)
        assert all(m.subject_ref == helper for m in r_help.memories)


# --- persistence across "restart" (fresh session) + surfaced in the prompt ----

async def test_npc_remembers_threat_in_a_later_session_prompt(db, provider):
    world = await build_world(db)
    listener = f"character:{world.kael_id}"
    # Session 1: the player threatens the guard.
    async with db.unit_of_work() as s:
        await NPCMemoryService(s).record_interaction(
            npc_id=world.guard_npc_id, listener_ref=listener, listener_name="Kael",
            utterance="จำไว้ ข้าจะขู่เจ้าถ้าเจ้าขวางทาง", game_time=50)

    # Later session (fresh DB session = process restart): the guard's prompt for the
    # SAME listener carries the remembered threat + fearful stance.
    async with db.session() as read:
        guard = await read.get(NPC, world.guard_npc_id)
        messages = await build_npc_response_context(
            read, npc=guard, listener_ref=listener, utterance="เปิดประตูให้ข้าหน่อย",
            listener_name="Kael", game_time=5000)
    blob = "\n".join(m["content"] for m in messages)
    assert "MEMORY_OF_LISTENER" in blob
    assert "THREAT" in blob
    assert "Kael" in blob


async def test_unwitnessed_npc_has_no_memory_of_the_event(db, provider):
    """An NPC that never interacted has an EMPTY recall — knowledge is not ambient.
    Structural: memories are scoped to the npc that experienced them."""
    world = await build_world(db)
    listener = f"character:{world.kael_id}"
    # Only the guard experiences the threat.
    async with db.unit_of_work() as s:
        await NPCMemoryService(s).record_interaction(
            npc_id=world.guard_npc_id, listener_ref=listener, listener_name="Kael",
            utterance="ข้าจะขู่เจ้า")
        # A second, uninvolved NPC.
        from app.npcs import NPCService
        bystander = await NPCService(s).create_npc(
            campaign_id=world.campaign_id, name="พ่อค้าเร่", personality="ร่าเริง")
        bystander_id = bystander.id
    async with db.session() as s:
        recalled = await NPCMemoryService(s).recall(npc_id=bystander_id, listener_ref=listener)
        assert recalled.memories == []
        assert recalled.relationship is None


# --- through the real social service -------------------------------------------

async def test_social_service_records_memory_and_thanks_the_right_character(db, provider):
    """Through NPCSocialService: helping raises the helper's standing; the recorded
    memory is about the exact character who helped — not some other party member."""
    world = await build_world(db)
    helper = f"character:{world.kael_id}"
    social = NPCSocialService(db, provider)
    result = await social.respond(
        campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
        listener_ref=helper, utterance="ข้าจะช่วยรักษาบาดแผลของเจ้าเอง")
    assert result.memory_type == "HELP"
    assert result.stance in ("grateful", "friendly", "loyal", "warm")
    async with db.session() as s:
        mems = (await s.execute(select(NPCMemory).where(
            NPCMemory.npc_id == world.guard_npc_id))).scalars().all()
        assert len(mems) == 1
        assert mems[0].subject_ref == helper          # the exact helper, not another PC
        rel = (await s.execute(select(NPCRelationship).where(
            NPCRelationship.npc_id == world.guard_npc_id,
            NPCRelationship.entity_ref == helper))).scalar_one()
        assert rel.obligation > 0 and rel.trust > 0
