"""A change of subject does not wipe the record.

The reported bug, exactly: Neneko goes for the goblins' map, is caught, then talks
about a cockroach — and the goblins carry on as though nothing happened. Their
suspicion, attitude and intentions never move; the excuse REPLACES the theft instead
of responding to it.

The cause was not narration and not summarisation. The event ledger recorded the
theft correctly, but nothing that drives NPC behaviour ever read it:

  * `record_interaction` / `record_typed_memory` were called only from the SOCIAL
    path, so a witnessed ACTION wrote nothing to NPCMemory/NPCRelationship;
  * `raise_suspicion` wrote a campaign-wide `attitudes["suspicion_level"]` counter
    that NPCDecisionService never reads (it reads NPCRelationship + NPCMemory);
  * `Event.witnesses` existed but the action path never populated it.

So `recall()` returned an empty slate and the goblin greeted the thief like a
stranger. These tests pin the closed loop end-to-end through the production bridge.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import InboundMessage
from app.engine import build_bridge
from app.models.enums import EventType, ResolutionType
from app.models.event import Event
from app.models.npc import NPC
from app.models.npc_epistemic import NPCMemory, NPCRelationship
from app.npcs.decision_service import NPCDecisionService
from app.npcs.memory_service import NPCMemoryService
from app.schemas.llm_io import ActionInterpretation, AdjudicationDecision
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, mid=None):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=mid or f"nc{_n['v']}", guild_id="guild-1",
        channel_id="chan-1", author_discord_id="disc-p1",
        author_display_name="Neneko", content=content,
    )


def _steals(provider):
    """Neneko goes for the map: a Sleight of Hand check aimed at the goblins' map."""
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ขโมยแผนที่", method="ล้วงเอาแผนที่", intent_confidence=0.9,
        target_references=["ยามเฝ้าประตู"], object_reference="แผนที่"))
    provider.on("adjudicate_uncertain_action", lambda m, model: AdjudicationDecision(
        resolution_type=ResolutionType.ABILITY_CHECK, ability="dex",
        skill="sleight_of_hand", dc_band="MEDIUM", rationale="ล้วงแผนที่"))


def _excuses(provider):
    """"Sorry — cockroach!" A social line, which the model reads as ordinary chat."""
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="แก้ตัวเรื่องแมลงสาบ", method="พูดกลบเกลื่อน", intent_confidence=0.9,
        social_intent=True, social_uncertain=False,       # the model says: just chat
        target_references=["ยามเฝ้าประตู"]))
    provider.on("adjudicate_uncertain_action", lambda m, model: AdjudicationDecision(
        resolution_type=ResolutionType.ABILITY_CHECK, ability="cha", skill="deception",
        dc_band="MEDIUM", rationale="แก้ตัว"))


async def _rel(db, world) -> NPCRelationship | None:
    async with db.session() as s:
        return (await s.execute(select(NPCRelationship).where(
            NPCRelationship.npc_id == world.guard_npc_id,
            NPCRelationship.entity_ref == f"character:{world.kael_id}",
        ))).scalars().first()


async def _memories(db, world) -> list[NPCMemory]:
    async with db.session() as s:
        return list((await s.execute(select(NPCMemory).where(
            NPCMemory.npc_id == world.guard_npc_id))).scalars())


def _table(db, provider, rolls=None):
    return build_bridge(db, provider=provider,
                        rng=SequenceRandomness(rolls or [], default=3))


# --- 1: a witnessed action becomes a memory --------------------------------------

async def test_a_caught_theft_is_remembered_by_the_witness(db, provider):
    """The core failure: this used to record NOTHING against the goblin."""
    world = await build_world(db)
    await start_session_with_scene(db, world)
    _steals(provider)

    # 3 + mod vs DC 15 -> failure -> caught.
    await _table(db, provider, [3]).handle_inbound(_msg("! ล้วงแผนที่จากยาม"))

    memories = await _memories(db, world)
    assert len(memories) == 1, "the witness must remember the attempt"
    memory = memories[0]
    assert memory.memory_type == "CAUGHT_STEALING"
    assert memory.subject_ref == f"character:{world.kael_id}"
    assert memory.witnessed_directly is True
    assert memory.importance >= 80, "being robbed is not a footnote"
    assert memory.open_question, "the guard is left with something to ask"
    assert memory.resolved is False


async def test_a_caught_theft_moves_the_relationship_that_drives_behaviour(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    _steals(provider)

    await _table(db, provider, [3]).handle_inbound(_msg("! ล้วงแผนที่จากยาม"))

    rel = await _rel(db, world)
    assert rel is not None
    assert rel.suspicion >= 40 and rel.trust <= -30
    # The stance the NPC decision path reads has actually changed.
    assert rel.current_stance in ("hostile", "wary", "guarded")


async def test_the_action_event_records_who_saw_it(db, provider):
    """`Event.witnesses` existed but the action path never filled it in."""
    world = await build_world(db)
    await start_session_with_scene(db, world)
    _steals(provider)

    await _table(db, provider, [3]).handle_inbound(_msg("! ล้วงแผนที่จากยาม"))

    async with db.session() as s:
        events = (await s.execute(select(Event).where(
            Event.event_type == EventType.PLAYER_ACTION_COMMITTED.value
        ))).scalars().all()
    event = events[-1]
    assert event.witnesses == [f"npc:{world.guard_npc_id}"]
    assert event.payload["action_class"] == "steal"
    assert event.payload["detection"] == "witnessed"
    assert event.payload["outcome"] == "failure"


async def test_an_unwitnessed_action_creates_no_memory(db, provider):
    """A consequence must not be invented either: with nobody there, nobody remembers.
    (The guard is the only NPC; move him out of the room.)"""
    world = await build_world(db)
    await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        guard = await s.get(NPC, world.guard_npc_id)
        guard.current_location_id = None
    _steals(provider)

    await _table(db, provider, [3]).handle_inbound(_msg("! ล้วงแผนที่จากยาม"))

    assert await _memories(db, world) == []


# --- 2: the memory changes what the NPC does -------------------------------------

async def test_the_witness_no_longer_treats_the_thief_as_a_stranger(db, provider):
    """The visible symptom: 'the goblins continued treating Neneko normally'."""
    world = await build_world(db)
    await start_session_with_scene(db, world)
    _steals(provider)
    await _table(db, provider, [3]).handle_inbound(_msg("! ล้วงแผนที่จากยาม"))

    async with db.session() as s:
        guard = await s.get(NPC, world.guard_npc_id)
        decision = await NPCDecisionService(s).decide(
            npc=guard, listener_ref=f"character:{world.kael_id}",
            utterance="สวัสดีครับ")

    assert decision.recognized_listener is True
    assert decision.current_stance in ("hostile", "wary", "guarded")
    assert decision.open_questions, "the guard is still waiting for an explanation"
    assert decision.followups, "suspicion must translate into intent"


async def test_suspicion_drives_escalating_follow_up_intentions(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    _steals(provider)
    await _table(db, provider, [3]).handle_inbound(_msg("! ล้วงแผนที่จากยาม"))

    async with db.session() as s:
        guard = await s.get(NPC, world.guard_npc_id)
        decision = await NPCDecisionService(s).decide(
            npc=guard, listener_ref=f"character:{world.kael_id}",
            utterance="สวัสดีครับ")

    # suspicion 40 -> watch + question + search, but not yet calling for help.
    assert "จับตาดูอย่างใกล้ชิด" in decision.followups
    assert "ซักถามให้ได้ความ" in decision.followups
    assert "ขอตรวจค้นตัว" in decision.followups
    assert "เรียกพวกมาเสริม" not in decision.followups


async def test_the_open_thread_reaches_the_dialogue_prompt(db, provider):
    """The generator must be TOLD the thread is open — it cannot infer it from prose
    it never saw."""
    world = await build_world(db)
    await start_session_with_scene(db, world)
    _steals(provider)
    await _table(db, provider, [3]).handle_inbound(_msg("! ล้วงแผนที่จากยาม"))

    async with db.session() as s:
        guard = await s.get(NPC, world.guard_npc_id)
        decision = await NPCDecisionService(s).decide(
            npc=guard, listener_ref=f"character:{world.kael_id}",
            utterance="แมลงสาบน่ะ")
    block = decision.as_prompt_block("เนเนโกะ")

    assert "ยังค้างคาใจ" in block
    assert "ห้ามลืม" in block
    assert "จะทำต่อไปนี้" in block


# --- 3: the excuse is contested, and cannot erase the theft ----------------------

async def test_an_excuse_to_a_suspicious_npc_is_not_free_dialogue(db, provider):
    """The exact mechanism of the bug: the model marks the cockroach line as ordinary
    chat (social_uncertain=False), which used to route it to free dialogue with no
    check. The ENGINE overrides, because it can see the open thread."""
    world = await build_world(db)
    await start_session_with_scene(db, world)
    _steals(provider)
    table = _table(db, provider, [3])
    await table.handle_inbound(_msg("! ล้วงแผนที่จากยาม"))

    _excuses(provider)
    table.pipeline.dice.rng = SequenceRandomness([18], default=18)
    result = await table.handle_inbound(_msg("! บอกยามว่าแค่ไล่แมลงสาบ"))

    # A roll happened: the excuse was contested, not accepted as conversation.
    roll_lines = [r.data["roll_line"] for r in result.responses
                  if r.data and r.data.get("roll_line")]
    assert roll_lines, f"the excuse must be rolled for; got {result.note!r}"
    assert "Deception" in roll_lines[0]


async def test_a_believed_excuse_closes_the_question_but_not_the_wound(db, provider):
    """'Even a successful excuse should not necessarily restore NPC trust
    completely.' It closes the question; it does not undo the theft."""
    world = await build_world(db)
    await start_session_with_scene(db, world)
    _steals(provider)
    table = _table(db, provider, [3])
    await table.handle_inbound(_msg("! ล้วงแผนที่จากยาม"))
    before = await _rel(db, world)
    trust_after_theft, suspicion_after_theft = before.trust, before.suspicion

    _excuses(provider)
    table.pipeline.dice.rng = SequenceRandomness([19], default=19)   # succeeds
    await table.handle_inbound(_msg("! บอกยามว่าแค่ไล่แมลงสาบ"))

    after = await _rel(db, world)
    assert after.suspicion < suspicion_after_theft, "a good story takes the edge off"
    assert after.suspicion > 0, "but he does not simply forget"
    assert after.trust <= trust_after_theft, "trust is not repaid by an excuse"
    # The memory itself survives — this is what 'the theft must remain a known event'
    # means in storage terms.
    memories = await _memories(db, world)
    theft = [m for m in memories if m.memory_type == "CAUGHT_STEALING"]
    assert theft and theft[0].active is True
    assert theft[0].resolved is True, "the question was answered"


async def test_a_failed_excuse_escalates_and_keeps_the_thread_open(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    _steals(provider)
    table = _table(db, provider, [3])
    await table.handle_inbound(_msg("! ล้วงแผนที่จากยาม"))
    before = await _rel(db, world)

    _excuses(provider)
    table.pipeline.dice.rng = SequenceRandomness([2], default=2)     # fails
    await table.handle_inbound(_msg("! บอกยามว่าแค่ไล่แมลงสาบ"))

    after = await _rel(db, world)
    assert after.suspicion > before.suspicion, "a botched lie makes it worse"
    assert after.trust < before.trust
    theft = [m for m in await _memories(db, world)
             if m.memory_type == "CAUGHT_STEALING"]
    assert theft[0].resolved is False, "he is still waiting for a real answer"


async def test_the_theft_still_colours_a_later_unrelated_interaction(db, provider):
    """'Future interactions must continue to reflect this event.' Even after the
    excuse is believed, the next conversation starts from what he saw."""
    world = await build_world(db)
    await start_session_with_scene(db, world)
    _steals(provider)
    table = _table(db, provider, [3])
    await table.handle_inbound(_msg("! ล้วงแผนที่จากยาม"))
    _excuses(provider)
    table.pipeline.dice.rng = SequenceRandomness([19], default=19)
    await table.handle_inbound(_msg("! บอกยามว่าแค่ไล่แมลงสาบ"))

    async with db.session() as s:
        guard = await s.get(NPC, world.guard_npc_id)
        decision = await NPCDecisionService(s).decide(
            npc=guard, listener_ref=f"character:{world.kael_id}",
            utterance="ขอถามทางหน่อย")

    assert decision.recognized_listener is True
    assert decision.current_stance != "neutral", (
        "he does not go back to treating her like a stranger")
    assert decision.followups, "he is still watching her"


# --- 4: the excuse does not need to be about the topic at all ---------------------

async def test_changing_the_subject_entirely_does_not_clear_the_thread(db, provider):
    """The cockroach is a change of TOPIC. Talking about the weather must not retire
    the question either — only addressing it can, and only via a check."""
    world = await build_world(db)
    await start_session_with_scene(db, world)
    _steals(provider)
    table = _table(db, provider, [3])
    await table.handle_inbound(_msg("! ล้วงแผนที่จากยาม"))

    # An ordinary, unrelated, non-social action.
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="มองไปรอบ ๆ", method="มอง", intent_confidence=0.9))
    provider.on("adjudicate_uncertain_action", lambda m, model: AdjudicationDecision(
        resolution_type=ResolutionType.ABILITY_CHECK, ability="wis", skill="perception",
        dc_band="EASY", rationale="มอง"))
    await table.handle_inbound(_msg("! มองไปรอบ ๆ ห้อง"))

    async with db.session() as s:
        unresolved = await NPCMemoryService(s).unresolved(
            npc_id=world.guard_npc_id, subject_ref=f"character:{world.kael_id}")
    assert unresolved, "an unrelated action cannot answer the guard's question"


# --- 5: idempotency ----------------------------------------------------------------

async def test_a_redelivered_message_does_not_double_the_grudge(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    _steals(provider)
    table = _table(db, provider, [3])

    await table.handle_inbound(_msg("! ล้วงแผนที่จากยาม", mid="same-1"))
    once = await _rel(db, world)
    suspicion_once = once.suspicion
    await table.handle_inbound(_msg("! ล้วงแผนที่จากยาม", mid="same-1"))

    assert len(await _memories(db, world)) == 1
    assert (await _rel(db, world)).suspicion == suspicion_once


# --- 6: the reported sequence, end to end ------------------------------------------

async def test_the_reported_sequence_end_to_end(db, provider):
    """Steal → caught → cockroach excuse. Assert every link the bug report says
    broke: the theft is known, the witness is recorded, suspicion moved, the excuse
    was contested, and the theft still matters afterwards."""
    world = await build_world(db)
    await start_session_with_scene(db, world)
    table = _table(db, provider, [3])

    # 1-2. Neneko goes for the map and is caught.
    _steals(provider)
    await table.handle_inbound(_msg("! ล้วงแผนที่จากยาม"))

    theft = [m for m in await _memories(db, world)
             if m.memory_type == "CAUGHT_STEALING"]
    assert theft, "the theft must remain a known event"
    assert theft[0].open_question

    # 3-4. She blames a cockroach. It is contested, not accepted.
    _excuses(provider)
    table.pipeline.dice.rng = SequenceRandomness([19], default=19)
    result = await table.handle_inbound(_msg("! บอกยามว่าแค่ไล่แมลงสาบ"))
    assert [r for r in result.responses if r.data and r.data.get("roll_line")]

    # 5-7. The theft did not stop mattering, and the guard's behaviour changed.
    rel = await _rel(db, world)
    assert rel.suspicion > 0 and rel.trust < 0
    async with db.session() as s:
        guard = await s.get(NPC, world.guard_npc_id)
        decision = await NPCDecisionService(s).decide(
            npc=guard, listener_ref=f"character:{world.kael_id}",
            utterance="ไม่มีอะไรหรอก")
    assert decision.current_stance != "neutral"
    assert decision.followups, "he watches her now"
    # 8. And the record of what she did is still there.
    assert [m for m in await _memories(db, world)
            if m.memory_type == "CAUGHT_STEALING" and m.active]
