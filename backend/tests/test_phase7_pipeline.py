"""Phase 7 acceptance: the committed-action pipeline resolves with server dice and
commits state+events atomically; the injected roll (not the LLM) drives the result;
illegal proposed deltas are rejected."""
from __future__ import annotations

from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import InboundMessage
from app.engine import build_bridge
from app.models.character import Character
from app.models.enums import EventType, Visibility
from app.models.event import Event
from app.models.npc import NPC
from app.schemas.llm_io import ConsequenceProposal, ProposedDelta
from app.models.enums import ConsequenceClass
from tests.support.factories import build_world, start_session_with_scene


def _inbound(mid, content, author="disc-p1"):
    return InboundMessage(
        discord_message_id=mid, guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name="กี้", content=content,
    )


async def _events(db, campaign_id, event_type=None):
    async with db.session() as s:
        stmt = select(Event).where(Event.campaign_id == campaign_id)
        if event_type:
            stmt = stmt.where(Event.event_type == event_type.value)
        return list((await s.execute(stmt.order_by(Event.seq))).scalars())


async def test_stealth_success_commits_state_and_events(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    # Kael: dex 16 (+3), stealth proficient (+2) => modifier +5. d20=14 => 19 vs DC15 => success.
    rng = SequenceRandomness([14])
    bridge = build_bridge(db, provider=provider, rng=rng)

    result = await bridge.handle_inbound(
        _inbound("s1", "! ผมค่อยๆ เดินไปดูตรงหน้าต่าง พยายามไม่ให้ยามเห็น")
    )
    assert result.state_mutated and "outcome=success" in result.note

    check_events = await _events(db, world.campaign_id, EventType.ABILITY_CHECK_RESOLVED)
    assert len(check_events) == 1
    mech = check_events[0].mechanical_changes
    # The natural roll is EXACTLY the injected die — proof the die came from the engine.
    assert mech["natural_roll"] == 14
    assert mech["modifier"] == 5
    assert mech["total"] == 19
    assert mech["outcome"] == "success"
    # A PLAYER_ACTION_COMMITTED event was also written, atomically.
    assert await _events(db, world.campaign_id, EventType.PLAYER_ACTION_COMMITTED)


async def test_stealth_failure_raises_guard_suspicion(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    rng = SequenceRandomness([3])  # 3+5=8 vs DC15 => failure
    bridge = build_bridge(db, provider=provider, rng=rng)

    result = await bridge.handle_inbound(
        _inbound("f1", "! ผมค่อยๆ เดินไปดูตรงหน้าต่าง พยายามไม่ให้ยามเห็น")
    )
    assert "outcome=failure" in result.note

    # The guard grew suspicious (DM-scoped) as a validated consequence delta.
    npc_events = await _events(db, world.campaign_id, EventType.NPC_STATE_CHANGED)
    assert len(npc_events) == 1
    assert npc_events[0].visibility == Visibility.DM_ONLY.value
    async with db.session() as s:
        guard = await s.get(NPC, world.guard_npc_id)
        assert guard.emotional_state == "ระแวง"
        assert (guard.attitudes or {}).get("suspicion_level") == 1


async def test_the_roll_not_the_llm_determines_outcome(db, provider):
    """Two identical-style stealth actions, two different injected rolls -> two
    different committed outcomes. The LLM script is identical; only the engine's die
    changed, proving the outcome is engine-owned."""
    world = await build_world(db)
    await start_session_with_scene(db, world)
    # First action pops 19 (success), second pops 3 (failure).
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([19, 3]))

    r1 = await bridge.handle_inbound(_inbound("r1", "! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    r2 = await bridge.handle_inbound(_inbound("r2", "! ผมย่องไปหน้าต่างอีกที ไม่ให้ยามเห็น"))
    assert "outcome=success" in r1.note
    assert "outcome=failure" in r2.note


async def test_pipeline_tolerates_real_model_ability_skill_vocabulary(db, provider):
    """A real LLM may return 'Dexterity'/'Stealth' (full/capitalized). The engine
    normalizes instead of crashing (regression: RulesViolation used to bubble up)."""
    from app.models.enums import DifficultyBand, ResolutionType
    from app.schemas.llm_io import AdjudicationDecision

    world = await build_world(db)
    await start_session_with_scene(db, world)
    provider.on(
        "adjudicate_uncertain_action",
        lambda m, model: AdjudicationDecision(
            resolution_type=ResolutionType.ABILITY_CHECK,
            ability="Dexterity", skill="Stealth", dc_band=DifficultyBand.MEDIUM,
        ),
    )
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))
    result = await bridge.handle_inbound(_inbound("v1", "! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    assert result.state_mutated and "outcome=success" in result.note

    check = (await _events(db, world.campaign_id, EventType.ABILITY_CHECK_RESOLVED))[0]
    assert check.payload["skill"] == "stealth"          # normalized
    assert check.mechanical_changes["modifier"] == 5    # +3 DEX, +2 proficiency
    assert check.mechanical_changes["total"] == 21


async def test_illegal_consequence_delta_is_rejected(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    # Force the planner to propose an out-of-authority HP mutation on failure.
    provider.on(
        "plan_consequence",
        lambda messages, model: ConsequenceProposal(
            consequence_class=ConsequenceClass.FAILURE_WITH_CONSEQUENCE,
            deltas=[ProposedDelta(kind="hp_change", target=f"character:{world.kael_id}",
                                  payload={"amount": -999})],
        ),
    )
    rng = SequenceRandomness([3])  # failure path triggers the consequence
    bridge = build_bridge(db, provider=provider, rng=rng)

    async with db.session() as s:
        hp_before = (await s.get(Character, world.kael_id)).hp

    result = await bridge.handle_inbound(_inbound("i1", "! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    assert result.state_mutated  # the action still commits

    async with db.session() as s:
        # The illegal HP delta was DROPPED — no damage applied.
        assert (await s.get(Character, world.kael_id)).hp == hp_before
    # No DAMAGE_APPLIED event was ever written from the hallucinated delta.
    assert await _events(db, world.campaign_id, EventType.DAMAGE_APPLIED) == []
