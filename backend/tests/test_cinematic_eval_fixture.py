"""Thai narrative evaluation fixture (issue #1, item H).

Structurally modeled on the attached cinematic-DM benchmark — dark character-focused
saving throw, established trauma used, a threatening entity acting with its own
agency, narration stopping cleanly before the roll, and a post-roll consequence that
changes the scene. Names, location, and plot are original (NOT Elias/Greyhaven/the
bell/the family-tragedy story from the benchmark).
"""
from __future__ import annotations

from app.core.randomness import SequenceRandomness
from app.discord_bridge import InboundMessage
from app.engine import build_bridge
from app.models.campaign import Campaign
from app.models.character import Character
from app.models.enums import ConsequenceClass, DifficultyBand, ResolutionType
from app.presentation import MessageKind
from app.schemas.llm_io import (
    ActionInterpretation,
    AdjudicationDecision,
    CheckSetup,
    ConsequenceProposal,
    Narration,
)
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1"):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"ev{_n['v']}", guild_id="g", channel_id="chan-1",
        author_discord_id=author, author_display_name="กี้", content=content,
    )


async def test_dark_saving_throw_scene_with_established_trauma_and_independent_threat(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        campaign = await s.get(Campaign, world.campaign_id)
        campaign.config = {**campaign.config, "dice_mode": "PLAYER_CLICK"}
        char = await s.get(Character, world.kael_id)
        # Established trauma: a stored hook, not something the benchmark or the
        # narrator invents on the spot.
        char.hooks = {
            **(char.hooks or {}),
            "fear": "กลัวเสียงนกหวีดเรือ ตั้งแต่คืนที่แม่หายไปกลางแม่น้ำ",
        }

    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ฟังเสียงนกหวีดที่ดังมาจากท่าเรือร้าง", method="ตั้งสติฟังอย่างระวังตัว",
        intent_confidence=0.9,
    ))
    provider.on("adjudicate_uncertain_action", lambda m, model: AdjudicationDecision(
        resolution_type=ResolutionType.SAVING_THROW, ability="wis",
        dc_band=DifficultyBand.HARD, rationale="เสียงนี้กระทบความทรงจำที่ฝังลึก",
    ))

    setup_captured = {}

    def _setup(messages, model):
        setup_captured["blob"] = "\n".join(
            m.get("content", "") for m in messages if m.get("role") == "user"
        )
        return CheckSetup(
            text=(
                "เสียงนกหวีดลากยาวจากท่าเรือร้างที่มืดสนิท\n"
                "สายหมอกคืบเข้ามาปกคลุมทางเดิน จนมองไม่เห็นอีกฝั่ง\n"
                "เจ้ารู้สึกถึงความคุ้นเคยบางอย่างในเสียงนั้น — บางอย่างที่ไม่อยากจำ"
            )
        )

    provider.on("generate_check_setup", _setup)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([3]))  # failure

    r = await bridge.handle_inbound(_msg("! ผมฟังเสียงนกหวีดที่ดังมาจากท่าเรือร้าง"))
    # CHECK_SETUP first: uses the established trauma, stops before the roll.
    assert r.responses[0].kind == MessageKind.CHECK_SETUP
    assert "เสียงนกหวีด" in r.responses[0].content
    assert "สำเร็จ" not in r.responses[0].content and "ล้มเหลว" not in r.responses[0].content
    assert "กลัวเสียงนกหวีดเรือ" in setup_captured["blob"]   # the actual stored hook, used
    assert r.responses[1].kind == MessageKind.CHECK_PROMPT

    provider.on("plan_consequence", lambda m, model: ConsequenceProposal(
        consequence_class=ConsequenceClass.FAILURE_WITH_CONSEQUENCE,
        narration_hint="ความทรงจำท่วมท้นจนตัวแข็ง ยามได้ยินเสียงเจ้าสะดุดและเริ่มเดินเข้ามา",
    ))
    provider.on("generate_dm_narration", lambda m, model: Narration(
        text=(
            "ภาพคืนนั้นแล่นผ่านหัวเจ้าอีกครั้ง — ร่างกายเจ้าแข็งค้าง เท้าสะดุดกับไม้กระดานท่าเรือ\n"
            "เสียงดังลั่นไปทั่วท่าเรือร้าง ยามเฝ้าประตูที่ไกลออกไปหันมาทันที เริ่มเดินตรงเข้ามา"
        ),
        decision_prompt="ยามเดินตรงเข้ามาหาเสียงนั้นแล้ว — Kael จะทำอย่างไร?",
    ))
    r = await bridge.handle_inbound(_msg("🎲 ทอย d20"))

    assert r.responses[0].kind == MessageKind.CHECK_RESOLUTION
    payoff = next(m for m in r.responses if m.kind == MessageKind.SCENE_FRAME)
    # The failure changed the scene (the guard now approaches) — not a bare "ล้มเหลว".
    assert "เดินตรงเข้ามา" in payoff.content
    assert payoff.content.strip() not in ("ล้มเหลว", "ไม่สำเร็จ")
    # Grounded decision addressed to the acting player character, referencing the
    # actual change — not the generic filler question.
    assert payoff.data["decision_prompt"] == "ยามเดินตรงเข้ามาหาเสียงนั้นแล้ว — Kael จะทำอย่างไร?"
    assert payoff.data["decision_prompt"] != "จะทำอะไรต่อ?"
