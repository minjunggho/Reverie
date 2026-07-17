"""Post-roll payoff: the committed consequence (class + narration_hint), pacing, and
bounded character context must reach the narrator — and the resulting decision prompt
must be grounded in the changed scene, not a generic filler question (issue #1, items
A/C/E)."""
from __future__ import annotations

from app.core.randomness import SequenceRandomness
from app.discord_bridge import InboundMessage
from app.engine import build_bridge
from app.models.campaign import Campaign
from app.models.character import Character
from app.models.enums import ConsequenceClass, DifficultyBand, ResolutionType
from app.presentation import MessageKind
from app.schemas.llm_io import AdjudicationDecision, ConsequenceProposal, Narration, ProposedDelta
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1"):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"pp{_n['v']}", guild_id="g", channel_id="chan-1",
        author_discord_id=author, author_display_name="กี้", content=content,
    )


async def _ritual_world(db):
    world = await build_world(db)
    session_id, scene_id = await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        campaign = await s.get(Campaign, world.campaign_id)
        campaign.config = {**campaign.config, "dice_mode": "PLAYER_CLICK"}
    return world, session_id, scene_id


async def test_consequence_class_and_narration_hint_reach_the_narrator(db, provider):
    world, _, _ = await _ritual_world(db)
    provider.on("plan_consequence", lambda m, model: ConsequenceProposal(
        consequence_class=ConsequenceClass.FAILURE_WITH_CONSEQUENCE,
        deltas=[], narration_hint="รองเท้าครูดพื้น ยามหันมาเต็มตัว",
    ))
    captured = {}

    def _capture(messages, model):
        captured["blob"] = "\n".join(m.get("content", "") for m in messages)
        return Narration(text="ยามหันมาเต็มตัว มือแตะด้ามดาบ", decision_prompt="ยามเห็นตัวเจ้าแล้ว — Kael จะทำอย่างไร?")

    provider.on("generate_dm_narration", _capture)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([3]))  # failure

    await bridge.handle_inbound(_msg("! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    r = await bridge.handle_inbound(_msg("🎲 ทอย d20"))

    assert "CONSEQUENCE_CLASS: ConsequenceClass.FAILURE_WITH_CONSEQUENCE" in captured["blob"] \
        or "FAILURE_WITH_CONSEQUENCE" in captured["blob"]
    assert "รองเท้าครูดพื้น ยามหันมาเต็มตัว" in captured["blob"]
    assert "NARRATIVE_PACING" in captured["blob"]
    payoff = next(m for m in r.responses if m.kind == MessageKind.SCENE_FRAME)
    assert payoff.data["decision_prompt"] == "ยามเห็นตัวเจ้าแล้ว — Kael จะทำอย่างไร?"


async def test_failed_stealth_changes_the_scene_not_just_fails(db, provider):
    world, _, _ = await _ritual_world(db)
    provider.on("plan_consequence", lambda m, model: ConsequenceProposal(
        consequence_class=ConsequenceClass.FAILURE_WITH_CONSEQUENCE,
        deltas=[ProposedDelta(kind="raise_suspicion", target=f"npc:{world.guard_npc_id}",
                              payload={"amount": 1}, reason="ได้ยินเสียง")],
        narration_hint="ยามได้ยินเสียงและเริ่มเดินมาทางนี้",
    ))
    provider.on("generate_dm_narration", lambda m, model: Narration(
        text="รองเท้าของเจ้าครูดกับพื้นหิน ยามหันขวับมาทางเสียงนั้นทันที เริ่มเดินเข้ามาใกล้",
        decision_prompt="ยามเดินตรงเข้ามาหาจุดที่เจ้าซ่อนอยู่ — เจ้าจะทำอย่างไร?",
    ))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([3]))

    await bridge.handle_inbound(_msg("! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    r = await bridge.handle_inbound(_msg("🎲 ทอย d20"))

    payoff = next(m for m in r.responses if m.kind == MessageKind.SCENE_FRAME)
    assert payoff.content.strip() != "ล้มเหลว"
    assert "ไม่สำเร็จ" not in payoff.content
    assert "เดินเข้ามาใกล้" in payoff.content            # the world visibly changed
    assert payoff.data["decision_prompt"] != "จะทำอะไรต่อ?"  # grounded, not generic


async def test_wis_save_with_established_trauma_used_in_setup_and_payoff(db, provider):
    world, _, _ = await _ritual_world(db)
    async with db.unit_of_work() as s:
        char = await s.get(Character, world.kael_id)
        char.hooks = {**(char.hooks or {}), "fear": "กลัวเสียงระฆังตั้งแต่บ้านไฟไหม้ตอนเด็ก"}

    provider.on("adjudicate_uncertain_action", lambda m, model: AdjudicationDecision(
        resolution_type=ResolutionType.SAVING_THROW, ability="wis",
        dc_band=DifficultyBand.MEDIUM, rationale="เสียงระฆังกระทบใจ",
    ))

    setup_captured = {}

    def _capture_setup(messages, model):
        setup_captured["blob"] = "\n".join(m.get("content", "") for m in messages)
        from app.schemas.llm_io import CheckSetup
        return CheckSetup(text="เสียงระฆังดังขึ้นอีกครั้ง เจ้ารู้สึกถึงความคุ้นเคยที่ไม่อยากจำ")

    provider.on("generate_check_setup", _capture_setup)

    payoff_captured = {}

    def _capture_narration(messages, model):
        payoff_captured["blob"] = "\n".join(m.get("content", "") for m in messages)
        return Narration(text="เจ้าตั้งสติไว้ได้ ทนต่อเสียงระฆังนั้น", decision_prompt="เสียงระฆังยังดังอยู่ — เจ้าจะทำอย่างไร?")

    provider.on("generate_dm_narration", _capture_narration)

    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))
    r = await bridge.handle_inbound(_msg("! ผมได้ยินเสียงระฆังดังขึ้นอีกครั้ง"))
    assert r.responses[0].kind == MessageKind.CHECK_SETUP
    assert "กลัวเสียงระฆังตั้งแต่บ้านไฟไหม้ตอนเด็ก" in setup_captured["blob"]
    # No outcome revealed pre-roll.
    assert "สำเร็จ" not in r.responses[0].content and "ล้มเหลว" not in r.responses[0].content

    await bridge.handle_inbound(_msg("🎲 ทอย d20"))
    assert "กลัวเสียงระฆังตั้งแต่บ้านไฟไหม้ตอนเด็ก" in payoff_captured["blob"]
    assert "NARRATIVE_PACING: NarrativePacing.CINEMATIC" in payoff_captured["blob"] \
        or "CINEMATIC" in payoff_captured["blob"]


async def test_wis_save_without_stored_trauma_invents_nothing(db, provider):
    world, _, _ = await _ritual_world(db)
    # Kael has no hooks at all.
    provider.on("adjudicate_uncertain_action", lambda m, model: AdjudicationDecision(
        resolution_type=ResolutionType.SAVING_THROW, ability="wis",
        dc_band=DifficultyBand.MEDIUM, rationale="สถานการณ์กดดัน",
    ))
    setup_captured = {}

    def _capture_setup(messages, model):
        # Only the USER content is actual data — the system prompt legitimately lists
        # these words as prohibitions ("ห้ามสร้างคู่รัก ลูก...").
        setup_captured["blob"] = "\n".join(
            m.get("content", "") for m in messages if m.get("role") == "user"
        )
        from app.schemas.llm_io import CheckSetup
        return CheckSetup(text="ความกดดันบีบเข้ามา แต่เจ้ายังตั้งหลักอยู่ได้")

    provider.on("generate_check_setup", _capture_setup)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))
    await bridge.handle_inbound(_msg("! ผมพยายามตั้งสติ"))

    for forbidden in ("คู่รัก", "ลูก", "พ่อแม่", "บาดแผล", "คำพยากรณ์", "เทพ"):
        assert forbidden not in setup_captured["blob"]
