"""Session 1 opening context wiring (issue #1, item F).

Campaign.brief/central_question, character appearance, and present NPCs must reach
the opening prompt when stored — and the opening must never invent content for
fields that are missing. Also: long openings must split across Discord embeds
instead of being silently truncated.
"""
from __future__ import annotations

import discord

from app.discord_bridge.dto import OutboundMessage
from app.models.campaign import Campaign
from app.models.character import Character
from app.models.enums import SceneMode
from app.presentation import MessageKind
from app.schemas.llm_io import CampaignPrologue, OpeningScene
from app.services.sessions import SessionOpeningService
from discord_bot.render import EMBED_DESC_LIMIT, build_embeds
from tests.support.factories import build_world


async def test_opening_uses_campaign_brief_and_central_question_when_stored(db, provider):
    """On this branch, brief/central_question feed the cinematic CampaignPrologue
    (main_goal falls back to central_question) rather than the plain OpeningScene —
    a strictly richer mechanism than a flat text marker, but it must still receive
    the same underlying data, plus character appearance."""
    world = await build_world(db)
    async with db.unit_of_work() as s:
        campaign = await s.get(Campaign, world.campaign_id)
        campaign.brief = "นครที่เคยรุ่งเรืองกำลังจมลงใต้เงาของโรคระบาดปริศนา"
        campaign.central_question = "ใครกันแน่ที่ปล่อยโรคระบาดนี้ออกมา"
        char = await s.get(Character, world.kael_id)
        char.appearance = "ผมสีเงิน ตาข้างซ้ายเป็นแผลเป็นเก่า"
        char.hooks = {**(char.hooks or {}), "desire": "อยากล้างชื่อครอบครัวที่ถูกใส่ร้าย"}

    captured = {}

    def _capture(messages, model):
        captured["blob"] = "\n".join(m.get("content", "") for m in messages)
        return CampaignPrologue(
            title="ทดสอบ", world="-", powers="-", crisis="-", approach="-",
            the_party="-", main_goal="ทดสอบ", first_beat="ทดสอบ",
            decision_prompt="จะทำอะไรก่อน?",
        )

    provider.on("generate_campaign_prologue", _capture)
    opener = SessionOpeningService(db, provider)
    await opener.open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=[world.p1_member_id, world.p2_member_id],
        location_id=world.location_id, mode=SceneMode.EXPLORATION,
    )

    assert "นครที่เคยรุ่งเรืองกำลังจมลงใต้เงาของโรคระบาดปริศนา" in captured["blob"]
    assert "ใครกันแน่ที่ปล่อยโรคระบาดนี้ออกมา" in captured["blob"]
    assert "ผมสีเงิน ตาข้างซ้ายเป็นแผลเป็นเก่า" in captured["blob"]


async def test_opening_does_not_invent_a_brief_when_none_is_stored(db, provider):
    world = await build_world(db)   # Campaign.brief/central_question default to ""
    captured = {}

    def _capture(messages, model):
        # Only the USER content is the data payload — the system prompt legitimately
        # mentions these marker names as "use them when present".
        captured["blob"] = "\n".join(
            m.get("content", "") for m in messages if m.get("role") == "user"
        )
        return OpeningScene(title="เริ่มต้น", situation_lines=["ทดสอบ"], decision_prompt="จะทำอะไรก่อน?")

    provider.on("generate_session_opening", _capture)
    opener = SessionOpeningService(db, provider)
    await opener.open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=[world.p1_member_id, world.p2_member_id],
        location_id=world.location_id, mode=SceneMode.EXPLORATION,
    )
    assert "CAMPAIGN_BRIEF" not in captured["blob"]
    assert "CENTRAL_QUESTION" not in captured["blob"]


async def test_present_npcs_at_the_opening_location_are_passed(db, provider):
    world = await build_world(db)   # guard_npc_id is placed at world.location_id
    captured = {}

    def _capture(messages, model):
        captured["blob"] = "\n".join(
            m.get("content", "") for m in messages if m.get("role") == "user"
        )
        return OpeningScene(title="เริ่มต้น", situation_lines=["ทดสอบ"], decision_prompt="จะทำอะไรก่อน?")

    provider.on("generate_session_opening", _capture)
    opener = SessionOpeningService(db, provider)
    await opener.open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=[world.p1_member_id, world.p2_member_id],
        location_id=world.location_id, mode=SceneMode.EXPLORATION,
    )
    assert "ยามเฝ้าประตู" in captured["blob"]


def test_long_opening_splits_across_embeds_without_truncation():
    """A cinematic opening longer than the embed description limit must be split on
    line boundaries, not silently truncated (issue #1, item F)."""
    long_lines = [f"บรรทัดที่ {i} เป็นเนื้อเรื่องยาวพอสมควรเพื่อดันให้เกินขีดจำกัด" for i in range(400)]
    content = "\n".join(long_lines)
    assert len(content) > EMBED_DESC_LIMIT

    msg = OutboundMessage("chan-1", content, kind=MessageKind.SCENE_FRAME,
                          data={"decision_prompt": "จะทำอะไรต่อ?"})
    embeds = build_embeds(msg)
    assert len(embeds) > 1
    for e in embeds:
        assert isinstance(e, discord.Embed)
        assert len(e.description or "") <= EMBED_DESC_LIMIT

    # No line was cut mid-sentence: every line from the source appears intact in the
    # reassembled description.
    reassembled = "\n".join(e.description or "" for e in embeds)
    for line in long_lines:
        assert line in reassembled

    # Structured fields (decision prompt) land on the LAST embed only.
    assert embeds[-1].fields
    assert not embeds[0].fields
