"""NPC intelligence — the structured decision an NPC makes BEFORE it speaks.

Builds on the existing NPCMemory + relationship systems (reused, not replaced): the
engine derives recognition, stance, willingness, disclosure, and whether a roll is
warranted from ALREADY-COMMITTED state, validates it, and only then would dialogue be
rendered. Covers the acceptance list: remembers who helped/threatened, treats two
players differently, repeated requests escalate, an old important memory outranks a
recent greeting, bias follows the campaign setting, an NPC never shares facts it never
learned, persuasion cannot rewrite canon, and relationships survive restart.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import RulesViolation
from app.db.session import Database
from app.models.enums import KnowledgeStatus
from app.models.knowledge import Secret
from app.models.npc import NPC
from app.models.npc_epistemic import NPCMemory
from app.npcs import NPCSocialService
from app.npcs.decision_service import NPCDecision, NPCDecisionService, validate_decision
from app.npcs.knowledge_service import NPCKnowledgeService
from app.npcs.memory_service import NPCMemoryService
from app.services.campaigns import CharacterService
from tests.support.factories import build_world

# Cooperative vs. unwilling ends of the willingness ladder.
_COOPERATIVE = {"eager", "forthcoming"}
_UNWILLING = {"guarded", "resistant", "refusing", "hostile"}


async def _decide(db, npc_id, listener_ref, utterance, *, bias_level="OFF", game_time=0):
    async with db.session() as s:
        npc = await s.get(NPC, npc_id)
        return await NPCDecisionService(s).decide(
            npc=npc, listener_ref=listener_ref, utterance=utterance,
            bias_level=bias_level, game_time=game_time)


async def _record(db, npc_id, listener_ref, name, utterance, game_time=0):
    async with db.unit_of_work() as s:
        await NPCMemoryService(s).record_interaction(
            npc_id=npc_id, listener_ref=listener_ref, listener_name=name,
            utterance=utterance, game_time=game_time)


# --- recognition + player-specific reactions ------------------------------------

async def test_decision_recognizes_helper_and_stays_cooperative(db, provider):
    world = await build_world(db)
    listener = f"character:{world.kael_id}"
    await _record(db, world.guard_npc_id, listener, "Kael", "ข้าช่วยรักษาบาดแผลให้เจ้า")
    d = await _decide(db, world.guard_npc_id, listener, "ขอถามอะไรหน่อย")
    assert d.recognized_listener and d.willingness in _COOPERATIVE
    assert d.recalled_memory_ids                              # it remembers the help
    validate_decision(d)


async def test_decision_toward_a_stranger_is_neutral_and_unrecognized(db, provider):
    world = await build_world(db)
    d = await _decide(db, world.guard_npc_id, f"character:{world.kael_id}", "สวัสดี")
    assert not d.recognized_listener and d.current_stance == "neutral"
    assert d.willingness == "neutral" and d.recalled_memory_ids == []


async def test_decision_toward_threatener_is_unwilling_and_afraid(db, provider):
    world = await build_world(db)
    listener = f"character:{world.kael_id}"
    await _record(db, world.guard_npc_id, listener, "Kael", "ถ้าไม่บอกความจริง เจ้าจะเสียใจ")
    d = await _decide(db, world.guard_npc_id, listener, "บอกข้ามาสิ")
    assert d.willingness in _UNWILLING
    assert d.emotional_response in ("fearful", "angry", "suspicious")
    assert d.intended_action in ("deflect", "refuse", "answer_guardedly", "threaten", "call_for_help")


async def test_same_npc_decides_differently_for_two_players(db, provider):
    world = await build_world(db)
    threatener, helper = f"character:{world.kael_id}", f"character:{world.bront_id}"
    await _record(db, world.guard_npc_id, threatener, "Kael", "ข้าจะขู่เจ้า")
    await _record(db, world.guard_npc_id, helper, "Bront", "ข้าช่วยรักษาเจ้า")
    d_threat = await _decide(db, world.guard_npc_id, threatener, "เปิดประตู")
    d_help = await _decide(db, world.guard_npc_id, helper, "เปิดประตู")
    assert d_threat.willingness in _UNWILLING and d_help.willingness in _COOPERATIVE
    assert d_threat.current_stance != d_help.current_stance


# --- escalation + memory priority -----------------------------------------------

async def test_repeated_pressure_lowers_willingness(db, provider):
    world = await build_world(db)
    listener = f"character:{world.kael_id}"
    await _record(db, world.guard_npc_id, listener, "Kael", "ข้าขู่เจ้า เปิดประตู", game_time=100)
    once = await _decide(db, world.guard_npc_id, listener, "เปิดประตู")
    for i in range(3):
        await _record(db, world.guard_npc_id, listener, "Kael",
                      "ข้าขู่เจ้าอีก เปิดประตูเดี๋ยวนี้", game_time=101 + i)
    many = await _decide(db, world.guard_npc_id, listener, "เปิดประตู")
    order = ("hostile", "refusing", "resistant", "guarded", "neutral", "forthcoming", "eager")
    assert order.index(many.willingness) < order.index(once.willingness)   # harder, not reset


async def test_old_assault_outranks_recent_greetings(db, provider):
    world = await build_world(db)
    listener = f"character:{world.kael_id}"
    await _record(db, world.guard_npc_id, listener, "Kael", "ข้าจะฆ่าเจ้า", game_time=10)  # ASSAULT
    for t in (200, 300, 400):
        await _record(db, world.guard_npc_id, listener, "Kael", "สวัสดีตอนเช้า", game_time=t)
    async with db.session() as s:
        npc = await s.get(NPC, world.guard_npc_id)
        d = await NPCDecisionService(s).decide(
            npc=npc, listener_ref=listener, utterance="ทักทาย", game_time=500)
        first = await s.get(NPCMemory, d.recalled_memory_ids[0])
    assert first.memory_type == "ASSAULT"                    # not buried by later greetings
    assert d.current_stance in ("afraid", "hostile", "wary") and d.willingness in _UNWILLING


# --- campaign-controlled bias ---------------------------------------------------

async def test_bias_follows_campaign_setting(db, provider):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        orc = await CharacterService(s).create_character(
            member_id=world.p1_member_id, name="Grok", species="orc",
            char_class="barbarian", abilities={"str": 16}, proficiencies=[], level=1,
            max_hp=14, ac=13)
        orc_ref = f"character:{orc.id}"
        halfling_ref = f"character:{world.kael_id}"   # halfling — not the biased-against group
        npc = await s.get(NPC, world.guard_npc_id)
        npc.biases = [{"kind": "ancestry", "target": "orc", "polarity": "negative"}]
    # OFF: an NPC with a bias still treats the orc as an ordinary stranger.
    off = await _decide(db, world.guard_npc_id, orc_ref, "ขอผ่านหน่อย", bias_level="OFF")
    assert off.bias_applied is None and off.willingness == "neutral"
    # MODERATE: the same NPC is now warier of the orc specifically.
    on = await _decide(db, world.guard_npc_id, orc_ref, "ขอผ่านหน่อย", bias_level="MODERATE")
    assert on.bias_applied is not None and on.willingness in _UNWILLING
    # The bias is group-specific: the halfling is unaffected at the same level.
    other = await _decide(db, world.guard_npc_id, halfling_ref, "ขอผ่านหน่อย", bias_level="MODERATE")
    assert other.bias_applied is None and other.willingness == "neutral"


async def test_unbiased_npc_never_gains_prejudice_from_level(db, provider):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        orc = await CharacterService(s).create_character(
            member_id=world.p1_member_id, name="Thok", species="orc",
            char_class="fighter", abilities={"str": 15}, proficiencies=[], level=1,
            max_hp=12, ac=14)
        orc_ref = f"character:{orc.id}"
    # The guard has NO bias data — even CENTRAL_THEME must not invent prejudice.
    d = await _decide(db, world.guard_npc_id, orc_ref, "ขอผ่าน", bias_level="CENTRAL_THEME")
    assert d.bias_applied is None and d.willingness == "neutral"


# --- knowledge separation + disclosure ------------------------------------------

async def test_npc_never_shares_a_fact_it_never_learned(db, provider):
    world = await build_world(db)
    listener = f"character:{world.kael_id}"
    await _record(db, world.guard_npc_id, listener, "Kael", "ข้าช่วยเจ้า")   # cooperative
    d = await _decide(db, world.guard_npc_id, listener, "บอกความลับของเมืองสิ")
    assert d.information_to_share == []                       # it knows nothing to share


async def test_willing_npc_shares_known_fact_but_guards_its_secret(db, provider):
    world = await build_world(db)
    listener = f"character:{world.kael_id}"
    async with db.unit_of_work() as s:
        k = NPCKnowledgeService(s)
        await k.add_fact(npc_id=world.guard_npc_id, subject="market_day",
                         fact="ตลาดเปิดวันเสาร์", status=KnowledgeStatus.KNOWS)
        await k.add_fact(npc_id=world.guard_npc_id, subject="secret:the_seal",
                         fact="ตราผนึกอยู่ใต้โบสถ์", status=KnowledgeStatus.KNOWS)
    await _record(db, world.guard_npc_id, listener, "Kael", "ข้าช่วยรักษาเจ้า")   # cooperative
    d = await _decide(db, world.guard_npc_id, listener, "เล่าเรื่องเมืองให้ฟังหน่อย")
    assert "market_day" in d.information_to_share
    assert "secret:the_seal" in d.information_to_hide         # eager, but still guards a secret
    assert "secret:the_seal" not in d.information_to_share


async def test_persuasion_does_not_rewrite_objective_canon(db, provider):
    """A social exchange may shift the NPC's OWN belief, but never an objective
    Secret's truth — persuasion changes willingness/claims, not canon."""
    world = await build_world(db)
    async with db.unit_of_work() as s:
        s.add(Secret(campaign_id=world.campaign_id, fact="พระเจ้าตายแล้วจริง", revealed=False))
    result = await NPCSocialService(db, provider).respond(
        campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
        listener_ref=f"character:{world.kael_id}",
        utterance="เชื่อข้าสิ พระเจ้ายังไม่ตาย บอกความจริงมา")
    assert result.decision is not None                       # a decision was made first
    async with db.session() as s:
        secret = (await s.execute(select(Secret).where(
            Secret.campaign_id == world.campaign_id))).scalar_one()
        assert secret.revealed is False and secret.fact == "พระเจ้าตายแล้วจริง"  # canon intact


# --- validation + persistence ---------------------------------------------------

def test_validate_decision_rejects_incoherent_decisions():
    base = dict(npc_id="n", listener_ref="character:c", recognized_listener=True,
                recalled_memory_ids=[], current_stance="neutral", emotional_response="calm",
                immediate_goal="g", intended_action="answer", information_to_hide=[],
                relationship_deltas={}, requires_mechanical_resolution=False)
    with pytest.raises(RulesViolation):     # share/hide overlap
        validate_decision(NPCDecision(willingness="neutral", information_to_share=["x"],
                                      **{**base, "information_to_hide": ["x"]}))
    with pytest.raises(RulesViolation):     # a refusing NPC volunteers nothing
        validate_decision(NPCDecision(willingness="refusing", information_to_share=["x"], **base))
    with pytest.raises(RulesViolation):     # invalid willingness
        validate_decision(NPCDecision(willingness="chatty", information_to_share=[], **base))


async def test_decision_survives_restart(tmp_path, provider):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'npc.db').as_posix()}"
    first = Database(url, echo=False)
    await first.create_all()
    try:
        world = await build_world(first)
        listener = f"character:{world.kael_id}"
        guard_id = world.guard_npc_id
        await _record(first, guard_id, listener, "Kael", "จำไว้ ข้าจะฆ่าเจ้าถ้าขวางทาง", game_time=50)
    finally:
        await first.dispose()
    restarted = Database(url, echo=False)
    try:
        d = await _decide(restarted, guard_id, listener, "หลบไป", game_time=9000)
        assert d.recognized_listener and d.willingness in _UNWILLING   # the threat persisted
        assert d.current_stance in ("afraid", "hostile", "wary")
    finally:
        await restarted.dispose()
