"""An NPC's plans outlive the turn that formed them — and it acts on them alone.

NPCDecisionService already derived follow-ups on an escalating, engine-owned ladder,
then recomputed them from scratch every turn and dropped them. So "NPC attitudes change
numerically without changing behavior" was exact: the numbers persisted in
NPCRelationship, the behaviour they implied did not. And because the path only ran when
an NPC was spoken TO, an NPC could never act while the party was elsewhere — it
answered, it never created movement (docs/progression-audit.md, RC6).
"""
from __future__ import annotations

from sqlalchemy import select

from app.models.campaign import Campaign
from app.models.npc import NPC
from app.models.npc_epistemic import NPCIntention
from app.npcs.decision_service import NPCDecisionService
from app.npcs.intention_service import NPCIntentionService
from app.npcs.memory_service import NPCMemoryService
from app.services.campaigns import CampaignService
from app.world.world_clock import WorldClockService

_LISTENER = "character:thief-1"


async def _world(db, channel: str) -> tuple[str, str]:
    """A campaign with one NPC. Returns (campaign_id, npc_id)."""
    async with db.unit_of_work() as s:
        camp = CampaignService(s)
        campaign = await camp.create_campaign(
            name="inn", discord_guild_id="g", game_channel_id=channel,
            owner_discord_user_id=f"owner-{channel}", owner_display_name="DM")
        await camp.activate_campaign(campaign.id)
        npc = NPC(campaign_id=campaign.id, name="เจ้าของโรงเตี๊ยม",
                  personality="ระแวง", goals=["ปกป้องของมีค่า"])
        s.add(npc)
        await s.flush()
        return campaign.id, npc.id


async def _suspicion(db, npc_id: str, value: int) -> None:
    async with db.unit_of_work() as s:
        rel = await NPCMemoryService(s)._relationship(npc_id, _LISTENER)
        rel.suspicion = value
        rel.familiarity = 10


async def _decide(db, npc_id: str):
    async with db.session() as s:
        npc = await s.get(NPC, npc_id)
        return await NPCDecisionService(s).decide(
            npc=npc, listener_ref=_LISTENER, utterance="ขอดูห้องหน่อย")


# --- follow-ups become persisted plans ------------------------------------------

async def test_derived_followups_persist_as_intentions(db, provider):
    cid, npc_id = await _world(db, "chan-intent")
    await _suspicion(db, npc_id, 60)          # far enough up the ladder to act

    decision = await _decide(db, npc_id)
    assert decision.followups, "sanity: the ladder derived something"

    async with db.unit_of_work() as s:
        await NPCIntentionService(s).sync_from_followups(
            npc_id=npc_id, subject_ref=_LISTENER, followups=decision.followups,
            game_time=0)
    async with db.session() as s:
        plans = await NPCIntentionService(s).pending_for(npc_id=npc_id)
    kinds = {p.kind for p in plans}
    assert "WATCH" in kinds and "MOVE_VALUABLES" in kinds
    assert all(p.state == "PENDING" for p in plans)


async def test_syncing_the_same_followups_twice_does_not_stack_duplicates(db, provider):
    """The ladder is re-derived every turn. Without idempotency an NPC accumulates six
    copies of 'watch them closely'."""
    cid, npc_id = await _world(db, "chan-dupe")
    await _suspicion(db, npc_id, 60)
    decision = await _decide(db, npc_id)

    for _ in range(3):
        async with db.unit_of_work() as s:
            await NPCIntentionService(s).sync_from_followups(
                npc_id=npc_id, subject_ref=_LISTENER, followups=decision.followups,
                game_time=0)
    async with db.session() as s:
        plans = await NPCIntentionService(s).pending_for(npc_id=npc_id)
    assert len(plans) == len({p.kind for p in plans})


async def test_plans_are_ordered_by_urgency(db, provider):
    cid, npc_id = await _world(db, "chan-urgency")
    await _suspicion(db, npc_id, 90)          # the whole ladder
    decision = await _decide(db, npc_id)
    async with db.unit_of_work() as s:
        await NPCIntentionService(s).sync_from_followups(
            npc_id=npc_id, subject_ref=_LISTENER, followups=decision.followups,
            game_time=0)
    async with db.session() as s:
        plans = await NPCIntentionService(s).pending_for(npc_id=npc_id)
    assert plans[0].kind == "BLOCK_EXIT"      # a cornered NPC does the urgent thing first
    assert [p.urgency for p in plans] == sorted([p.urgency for p in plans], reverse=True)


# --- the point of persisting: a decision survives a charming conversation --------

async def test_a_plan_survives_the_suspicion_that_created_it_being_talked_down(db, provider):
    """The behaviour that persistence buys. A thief who was caught and then charmed the
    innkeeper does not get a clean slate: the innkeeper already decided to move the
    strongbox, and a pleasant conversation does not unmake a decision. With the ladder
    recomputed from the current number each turn, it did.
    """
    cid, npc_id = await _world(db, "chan-grudge")
    await _suspicion(db, npc_id, 60)
    decision = await _decide(db, npc_id)
    async with db.unit_of_work() as s:
        await NPCIntentionService(s).sync_from_followups(
            npc_id=npc_id, subject_ref=_LISTENER, followups=decision.followups,
            game_time=0)

    await _suspicion(db, npc_id, 0)           # talked all the way down
    later = await _decide(db, npc_id)

    assert later.followups, "the standing plan must not evaporate with the feeling"
    assert "ย้ายของมีค่าให้พ้นมือ" in later.followups
    block = later.as_prompt_block("โจร")
    assert "ย้ายของมีค่าให้พ้นมือ" in block   # and the NPC is TOLD it still intends this


# --- NPCs act while the party is elsewhere --------------------------------------

async def test_an_npc_carries_out_its_plan_when_the_clock_comes_due(db, provider):
    """The line between a thing that answers questions and someone who does things.
    The innkeeper moves the strongbox on the CLOCK — not because anyone spoke to him."""
    cid, npc_id = await _world(db, "chan-offscreen")
    await _suspicion(db, npc_id, 60)
    decision = await _decide(db, npc_id)
    async with db.unit_of_work() as s:
        await NPCIntentionService(s).sync_from_followups(
            npc_id=npc_id, subject_ref=_LISTENER, followups=decision.followups,
            game_time=0)

    async with db.unit_of_work() as s:
        result = await WorldClockService(s).advance_time(campaign_id=cid, minutes=120)

    assert result.acted_intentions, "a due plan must fire on the clock"
    async with db.session() as s:
        rows = (await s.execute(select(NPCIntention).where(
            NPCIntention.npc_id == npc_id))).scalars().all()
    by_kind = {r.kind: r for r in rows}
    assert by_kind["MOVE_VALUABLES"].state == "FULFILLED"
    # A plan that needs the party present must NOT fire on a clock tick.
    assert by_kind["WATCH"].state == "PENDING"


async def test_an_npc_does_not_act_before_its_plan_is_due(db, provider):
    """Not instant: a shopkeeper who just decided to move the strongbox does it
    shortly, not mid-sentence."""
    cid, npc_id = await _world(db, "chan-early")
    await _suspicion(db, npc_id, 60)
    decision = await _decide(db, npc_id)
    async with db.unit_of_work() as s:
        await NPCIntentionService(s).sync_from_followups(
            npc_id=npc_id, subject_ref=_LISTENER, followups=decision.followups,
            game_time=0)
    async with db.unit_of_work() as s:
        result = await WorldClockService(s).advance_time(campaign_id=cid, minutes=1)
    assert result.acted_intentions == []


async def test_one_campaigns_npcs_never_act_on_another_campaigns_clock(db, provider):
    """Intentions hang off npc_id, which has no campaign column — the sweep must scope
    itself or every campaign's clock fires every other campaign's NPCs."""
    cid_a, npc_a = await _world(db, "chan-iso-a")
    cid_b, _npc_b = await _world(db, "chan-iso-b")
    await _suspicion(db, npc_a, 60)
    decision = await _decide(db, npc_a)
    async with db.unit_of_work() as s:
        await NPCIntentionService(s).sync_from_followups(
            npc_id=npc_a, subject_ref=_LISTENER, followups=decision.followups,
            game_time=0)

    async with db.unit_of_work() as s:
        result = await WorldClockService(s).advance_time(campaign_id=cid_b, minutes=120)
    assert result.acted_intentions == [], "campaign B's clock must not move campaign A's NPCs"

    async with db.session() as s:
        rows = await NPCIntentionService(s).pending_for(npc_id=npc_a)
    assert any(r.kind == "MOVE_VALUABLES" for r in rows)
