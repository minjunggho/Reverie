"""Campaign direction is present on EVERY turn — not just the Session 1 prologue.

Before this, `Campaign.main_story` was seeded at import and read once, by the opening
cinematic. The narration context carried a location, a cast, and the last action, so
from turn 2 onward the DM could only react to the last message — it was never told the
campaign had a direction at all (docs/progression-audit.md, RC1).

These tests pin the contract: the goal and open leads reach the narrator every turn,
the DM-only hidden truth never does, and direction that has been resolved stops being
presented as direction.
"""
from __future__ import annotations

from app.memory.context_builders import build_narration_context
from app.memory.progression_context import (
    MAX_VISIBLE_LEADS,
    ProgressionContext,
    ProgressionContextBuilder,
)
from app.models.campaign import Campaign
from app.services.campaigns import CampaignService
from app.services.campaigns.canon_import import CanonImportService

_HARBOR = """# Campaign: เมืองท่าที่เรือไม่ออก

## Brief
เมืองท่าที่เรือทุกลำมาถึงแต่ไม่มีลำไหนได้ออกไป

## Central Question
ใครหรืออะไรกักเรือทั้งเมืองไว้ และแลกกับอะไร

## Location: ท่าเรือเก่า
### key
old-harbor
### obvious
ท่าเรือหินที่เรือจอดแน่นขนัด
### exits
- ถนนริมน้ำ / ออกไป / 5 นาที -> waterfront-road

## Location: ถนนริมน้ำ
### key
waterfront-road
### exits
- กลับท่าเรือ / เข้าไป / 5 นาที -> old-harbor

## Secret: สัญญาใต้น้ำ
### key
the-pact
### truth
นายท่าขายทางออกของเมืองแลกกับชีวิตลูกสาว
### clues
- บันทึกในสมุดท่าเรือที่หน้าขาดไป

## Threat: น้ำที่สูงขึ้น
### key
rising-water
### goal
ท่วมเมืองภายในเจ็ดวัน
### next action
น้ำขึ้นสูงกว่าเดิมอีกหนึ่งศอก
"""


async def _import(db, channel: str, text: str) -> str:
    async with db.unit_of_work() as s:
        camp = CampaignService(s)
        campaign = await camp.create_campaign(
            name="import-target", discord_guild_id="g", game_channel_id=channel,
            owner_discord_user_id=f"owner-{channel}", owner_display_name="DM")
        await camp.activate_campaign(campaign.id)
        owner = await camp.resolve_member(campaign.id, f"owner-{channel}")
        draft = await CanonImportService(s).create_draft(
            campaign_id=campaign.id, uploader_member_id=owner.id,
            filename="c.md", data=text.encode("utf-8"))
        cid, did = campaign.id, draft.id
    async with db.unit_of_work() as s:
        await CanonImportService(s).approve(import_id=did, campaign_id=cid)
    return cid


# --- the read path: direction reaches the narrator ------------------------------

async def test_imported_campaign_goal_and_leads_reach_the_narration_prompt(db, provider):
    """The regression that mattered: goal + leads in the prompt on an ordinary turn."""
    cid = await _import(db, "chan-direction", _HARBOR)
    async with db.session() as s:
        ctx = await ProgressionContextBuilder(s).build(campaign_id=cid)
        assert "กักเรือทั้งเมือง" in ctx.campaign_goal
        assert ctx.leads, "an imported campaign with secrets/threats must expose leads"

        messages = await build_narration_context(
            s, action_text="มองไปรอบๆ", outcome="success", result_summary="-",
            scene=None, progression_context=ctx,
        )
    prompt = messages[1]["content"]
    assert "CAMPAIGN_DIRECTION" in prompt
    assert "กักเรือทั้งเมือง" in prompt          # the campaign goal is in view
    assert "OPEN_LEADS" in prompt


async def test_hidden_truth_never_enters_player_facing_direction(db, provider):
    """`main_story.hidden_truth` is the concealed answer to the dramatic question —
    DM-only. It shares a JSON blob with the goal and leads, so the ONLY thing keeping
    it out of a player-facing prompt is that this builder never reads it."""
    cid = await _import(db, "chan-secret", _HARBOR)
    async with db.session() as s:
        story = (await s.get(Campaign, cid)).main_story
        assert "ขายทางออกของเมือง" in story["hidden_truth"]   # it IS in the blob

        ctx = await ProgressionContextBuilder(s).build(campaign_id=cid)
        messages = await build_narration_context(
            s, action_text="มองไปรอบๆ", outcome="success", result_summary="-",
            scene=None, progression_context=ctx,
        )
    # ...and it is nowhere in what the narrator is handed.
    assert "ขายทางออกของเมือง" not in ctx.as_block()
    assert "ขายทางออกของเมือง" not in messages[1]["content"]


# --- direction that is resolved stops being direction ---------------------------

async def test_completed_main_goal_is_no_longer_presented_as_direction(db, provider):
    """A finished goal must stop steering the party at something already resolved.

    The subtle part: `central_question` is the fallback for campaigns that have no
    recorded goal, and it holds the SAME text the main goal was seeded from. So a
    naive fallback resurrects a completed goal through the back door. An explicit
    main goal must govern its own absence.
    """
    from app.services.campaigns.main_story import MainStoryService

    cid = await _import(db, "chan-done", _HARBOR)
    async with db.session() as s:
        before = await ProgressionContextBuilder(s).build(campaign_id=cid)
    assert before.campaign_goal, "sanity: an open main goal IS direction"

    async with db.unit_of_work() as s:
        await MainStoryService(s).set_goal_status(cid, "main", "completed")
    async with db.session() as s:
        after = await ProgressionContextBuilder(s).build(campaign_id=cid)
        campaign = await s.get(Campaign, cid)

    assert campaign.central_question, "sanity: the fallback text still exists"
    assert after.campaign_goal == "", "a completed goal must not be re-presented"


async def test_campaign_with_no_recorded_goal_falls_back_to_central_question(db, provider):
    """Campaigns imported before main_story existed still have a central question, and
    it is the only direction they have. The completed-goal rule must not break them."""
    async with db.unit_of_work() as s:
        camp = CampaignService(s)
        campaign = await camp.create_campaign(
            name="legacy", discord_guild_id="g", game_channel_id="chan-legacy",
            owner_discord_user_id="owner-legacy", owner_display_name="DM")
        campaign.central_question = "ใครเผาหอคอย"
        campaign.main_story = {}          # no goals recorded at all
        cid = campaign.id
    async with db.session() as s:
        ctx = await ProgressionContextBuilder(s).build(campaign_id=cid)
    assert ctx.campaign_goal == "ใครเผาหอคอย"


async def test_leads_are_bounded_so_the_party_sees_two_to_four_opportunities(db, provider):
    """The brief's pacing rule. An unbounded lead list turns direction into noise and
    lets the narrator point at a thread the party has no route to."""
    from app.services.campaigns.main_story import MainStoryService

    cid = await _import(db, "chan-many", _HARBOR)
    async with db.unit_of_work() as s:
        for i in range(12):
            await MainStoryService(s).add_lead(cid, f"lead-{i}")
    async with db.session() as s:
        ctx = await ProgressionContextBuilder(s).build(campaign_id=cid)
    assert len(ctx.leads) <= MAX_VISIBLE_LEADS


# --- absence of direction is silence, not an empty header -----------------------

async def test_campaign_without_direction_adds_no_block():
    """A hand-made campaign with no imported story must not get an empty scaffold."""
    ctx = ProgressionContext()
    assert ctx.has_direction is False
    assert ctx.as_block() == ""


async def test_direction_block_omits_unset_objective_layers():
    """chapter/objective stay empty until the objective layer exists (slice 2) — they
    must render nothing rather than an empty label."""
    ctx = ProgressionContext(campaign_goal="หาคนที่กักเรือ", leads=["ถามนายท่า"])
    block = ctx.as_block()
    assert "GOAL:" in block and "OPEN_LEADS" in block
    assert "CHAPTER:" not in block and "OBJECTIVE:" not in block
