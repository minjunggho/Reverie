"""Phase 11 acceptance: NPC prompts never receive objective truth the NPC hasn't
learned; belief/attitude deltas are AI-proposed and engine-committed."""
from __future__ import annotations

from sqlalchemy import func, select

from app.memory.context_builders import build_npc_response_context
from app.models.enums import EventType, KnowledgeStatus, Visibility
from app.models.event import Event
from app.models.npc import NPC
from app.models.npc_epistemic import NPCFact, NPCRelationship
from app.npcs import NPCKnowledgeService, NPCSocialService
from app.schemas.llm_io import NPCResponse, ProposedBeliefDelta
from tests.support.factories import build_world


async def test_npc_context_excludes_unlearned_objective_truth(db, provider):
    world = await build_world(db)
    # Objective truth (a Secret) that the guard has NOT learned.
    from app.models.knowledge import Secret

    async with db.unit_of_work() as s:
        s.add(Secret(campaign_id=world.campaign_id, fact="OBJECTIVE_ปาร์ตี้ฆ่ากัปตันเร็น",
                     visibility=Visibility.DM_ONLY.value))
        # What the guard actually knows / believes.
        know = NPCKnowledgeService(s)
        await know.add_fact(npc_id=world.guard_npc_id, subject="party",
                            fact="เห็นคนแปลกหน้าแถวที่เกิดเหตุ", status=KnowledgeStatus.KNOWS)
        await know.add_fact(npc_id=world.guard_npc_id, subject="party_hiding",
                            fact="พวกมันปิดบังอะไรบางอย่าง", status=KnowledgeStatus.BELIEVES)
        # A FORGOTTEN fact must not surface either.
        await know.add_fact(npc_id=world.guard_npc_id, subject="old",
                            fact="เรื่องเก่าที่ลืมไปแล้ว", status=KnowledgeStatus.FORGOTTEN)

    async with db.session() as read:
        guard = await read.get(NPC, world.guard_npc_id)
        messages = await build_npc_response_context(
            read, npc=guard, listener_ref=f"character:{world.kael_id}", utterance="สวัสดี"
        )
    blob = "\n".join(m["content"] for m in messages)
    assert "เห็นคนแปลกหน้า" in blob            # the NPC's own knowledge is present
    assert "ปิดบัง" in blob                     # its belief is present
    assert "OBJECTIVE_" not in blob             # objective truth it never learned is absent
    assert "เรื่องเก่าที่ลืม" not in blob        # FORGOTTEN facts are excluded


async def test_belief_and_attitude_deltas_are_engine_committed(db, provider):
    world = await build_world(db)
    listener = f"character:{world.kael_id}"
    # The AI proposes a belief change + an attitude change; the engine commits them.
    provider.on(
        "generate_npc_response",
        lambda m, model: NPCResponse(
            utterance="ยามเริ่มไม่ไว้ใจ",
            proposed_belief_deltas=[ProposedBeliefDelta(
                npc_id=world.guard_npc_id, subject="party_hiding",
                new_status=KnowledgeStatus.SUSPECTS.value, confidence=0.7, reason="ตอบเลี่ยง")],
            proposed_attitude="wary",
        ),
    )
    result = await NPCSocialService(db, provider).respond(
        campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
        listener_ref=listener, utterance="ข้าไม่ได้ทำอะไรนะ",
    )
    assert result.committed_belief_changes == ["party_hiding:SUSPECTS"]
    assert result.attitude_change == "wary"

    async with db.session() as s:
        # The belief record was persisted with the proposed status.
        fact = (
            await s.execute(
                select(NPCFact).where(
                    NPCFact.npc_id == world.guard_npc_id, NPCFact.subject == "party_hiding"
                )
            )
        ).scalar_one()
        assert fact.status == KnowledgeStatus.SUSPECTS.value
        # The relationship/attitude was persisted.
        rel = (
            await s.execute(
                select(NPCRelationship).where(NPCRelationship.npc_id == world.guard_npc_id)
            )
        ).scalar_one()
        assert rel.attitude == "wary"
        # Both changes are DM-scoped events (players don't auto-learn them).
        npc_events = (
            await s.execute(
                select(Event).where(
                    Event.campaign_id == world.campaign_id,
                    Event.event_type == EventType.NPC_STATE_CHANGED.value,
                )
            )
        ).scalars().all()
        assert len(npc_events) == 2
        assert all(e.visibility == Visibility.DM_ONLY.value for e in npc_events)


async def test_npc_cannot_update_another_npcs_beliefs(db, provider):
    world = await build_world(db)
    # A malicious/hallucinated proposal targeting a DIFFERENT npc id is ignored.
    provider.on(
        "generate_npc_response",
        lambda m, model: NPCResponse(
            utterance="...",
            proposed_belief_deltas=[ProposedBeliefDelta(
                npc_id="some-other-npc", subject="x",
                new_status=KnowledgeStatus.KNOWS.value)],
        ),
    )
    result = await NPCSocialService(db, provider).respond(
        campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
        listener_ref=f"character:{world.kael_id}", utterance="hi",
    )
    assert result.committed_belief_changes == []
    async with db.session() as s:
        count = (
            await s.execute(select(func.count(NPCFact.id)))
        ).scalar_one()
        assert count == 0  # nothing committed
