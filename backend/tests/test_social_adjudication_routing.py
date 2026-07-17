"""Social interaction routing (issue #1, item E + test cases 6/7).

Ordinary social intent (asking, greeting, thanking) stays free NPC dialogue decided
by the NPC's own knowledge/personality. Coercive/uncertain social intent (intimidate,
deceive, bargain against interest) routes through the SAME adjudication/dice path as
any other contested action — it never always becomes free dialogue.
"""
from __future__ import annotations

from app.core.randomness import SequenceRandomness
from app.discord_bridge import InboundMessage
from app.engine import build_bridge
from app.models.enums import DifficultyBand, ResolutionType
from app.presentation import MessageKind
from app.schemas.llm_io import ActionInterpretation, AdjudicationDecision
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1"):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"soc{_n['v']}", guild_id="g", channel_id="chan-1",
        author_discord_id=author, author_display_name="กี้", content=content,
    )


async def test_npc_advice_is_free_dialogue_and_npc_decides_for_itself(db, provider):
    world = await build_world(db)   # factory default: AUTO dice mode
    await start_session_with_scene(db, world)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ถามยามว่าทางไปห้องเก็บของอยู่ไหน", method="ถามตรงๆ",
        target_references=["ยามเฝ้าประตู"], intent_confidence=0.9,
        social_intent=True, social_uncertain=False,
    ))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))

    r = await bridge.handle_inbound(_msg("! ผมถามยามว่าห้องเก็บของอยู่ไหน"))
    assert r.responses[0].kind == MessageKind.NPC_DIALOGUE
    # The NPC's own line, not a question handing the choice back to the player.
    assert "?" not in r.responses[0].content or "จะ" not in r.responses[0].content
    assert r.responses[0].content != ""


async def test_intimidation_with_uncertain_outcome_routes_to_adjudication(db, provider):
    world = await build_world(db)   # AUTO mode -> resolves immediately
    await start_session_with_scene(db, world)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ข่มขู่ยามให้ยอมเปิดประตูลับ", method="จ่อดาบขู่",
        target_references=["ยามเฝ้าประตู"], intent_confidence=0.85,
        social_intent=True, social_uncertain=True,
    ))
    provider.on("adjudicate_uncertain_action", lambda m, model: AdjudicationDecision(
        resolution_type=ResolutionType.ABILITY_CHECK, ability="cha", skill="intimidation",
        dc_band=DifficultyBand.MEDIUM, rationale="ยามอาจกลัวหรือไม่ก็ได้",
    ))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))

    r = await bridge.handle_inbound(_msg("! ผมจ่อดาบข่มขู่ยามให้เปิดประตูลับ"))
    # NOT free dialogue — a real check happened.
    assert r.responses[0].kind == MessageKind.CHECK_RESOLUTION
    assert "outcome=success" in r.note
    async with db.session() as s:
        from sqlalchemy import select

        from app.models.enums import EventType
        from app.models.event import Event

        checks = (await s.execute(select(Event).where(
            Event.event_type == EventType.ABILITY_CHECK_RESOLVED.value))).scalars().all()
        assert len(checks) == 1
        assert checks[0].payload["skill"] == "intimidation"


async def test_intimidation_pauses_for_the_dice_ritual_under_player_click(db, provider):
    from app.models.campaign import Campaign

    world = await build_world(db)
    await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        campaign = await s.get(Campaign, world.campaign_id)
        campaign.config = {**campaign.config, "dice_mode": "PLAYER_CLICK"}

    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ข่มขู่ยามให้ยอมเปิดประตูลับ", method="จ่อดาบขู่",
        target_references=["ยามเฝ้าประตู"], intent_confidence=0.85,
        social_intent=True, social_uncertain=True,
    ))
    provider.on("adjudicate_uncertain_action", lambda m, model: AdjudicationDecision(
        resolution_type=ResolutionType.ABILITY_CHECK, ability="cha", skill="intimidation",
        dc_band=DifficultyBand.MEDIUM, rationale="ยามอาจกลัวหรือไม่ก็ได้",
    ))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))
    r = await bridge.handle_inbound(_msg("! ผมจ่อดาบข่มขู่ยามให้เปิดประตูลับ"))
    assert r.responses[0].kind == MessageKind.CHECK_SETUP
    assert r.responses[1].kind == MessageKind.CHECK_PROMPT
    assert r.state_mutated is False
