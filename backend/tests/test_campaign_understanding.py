"""Campaign understanding — the owner's story is read, preserved, and stays reactive.

Covers: deterministic validation (structural issues surfaced, not invented);
provenance priority (explicit owner canon outranks + is never overwritten by AI,
contradictions surfaced not resolved); main-story continuity (dramatic question,
leads, hidden truth, deadlines, player-caused branches — persisted, surviving
restart and many turns); campaign isolation (no leak between campaigns); and that
important imported canon is unchanged after import.
"""
from __future__ import annotations

from sqlalchemy import select

from app.models.campaign import Campaign
from app.models.location import Location
from app.models.npc import NPC
from app.models.knowledge import Secret
from app.services.campaigns import CampaignService
from app.services.campaigns.campaign_validation import validate_campaign
from app.services.campaigns.canon_import import CanonImportService, parse_campaign_file
from app.services.campaigns.main_story import MainStoryService
from app.services.campaigns.provenance import may_overwrite, outranks

# --- two UNRELATED campaign fixtures (structured Reverie Markdown) --------------

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

## NPC: นายท่าเฒ่า
### key
old-harbormaster
### goal
ปกปิดสัญญาที่เขาทำไว้กับสิ่งใต้น้ำ
### location
old-harbor

## Secret: สัญญาใต้น้ำ
### key
the-pact
### truth
นายท่าขายทางออกของเมืองแลกกับชีวิตลูกสาว
### clues
- บันทึกในสมุดท่าเรือที่หน้าขาดไป
- ชาวเรือเล่าว่าเห็นแสงใต้น้ำทุกคืนข้างแรม

## Threat: น้ำที่สูงขึ้น
### key
rising-water
### goal
ท่วมเมืองภายในเจ็ดวัน
### next action
น้ำสูงขึ้นอีกหนึ่งศอกทุกเที่ยงคืน
### scheduled
1440

## Session 1
### purpose
สืบว่าทำไมเรือออกไม่ได้
### opening location
old-harbor
"""

_ORCHARD = """# Campaign: สวนที่ผลไม้จำความได้

## Brief
หมู่บ้านสวนที่ผลไม้เก็บความทรงจำของคนที่ปลูก

## Central Question
ความทรงจำของใครถูกขโมยไปในผลไม้ฤดูนี้

## Location: สวนกลางหมู่บ้าน
### key
central-orchard
### obvious
แถวต้นไม้ที่ผลเรืองแสงจางๆ

## NPC: คนสวนตาบอด
### key
blind-gardener
### goal
หาความทรงจำของภรรยาที่หายไป
### location
central-orchard

## Secret: ต้นไม้ต้นแรก
### key
first-tree
### truth
ต้นไม้ต้นแรกกินความทรงจำเพื่อมีชีวิต
### clues
- รากที่ชอนไชไปใต้บ้านทุกหลัง
- ผลที่ร่วงมีเสียงกระซิบ

## Session 1
### purpose
เข้าใจว่าเกิดอะไรกับความทรงจำ
### opening location
central-orchard
"""


async def _import(db, world_channel_owner, text: str, filename="c.md"):
    """Create a draft from the raw file + approve it, returning the campaign id."""
    async with db.unit_of_work() as s:
        camp = CampaignService(s)
        campaign = await camp.create_campaign(
            name="import-target", discord_guild_id="g", game_channel_id=world_channel_owner,
            owner_discord_user_id=f"owner-{world_channel_owner}", owner_display_name="DM")
        await camp.activate_campaign(campaign.id)
        owner = await camp.resolve_member(campaign.id, f"owner-{world_channel_owner}")
        draft = await CanonImportService(s).create_draft(
            campaign_id=campaign.id, uploader_member_id=owner.id,
            filename=filename, data=text.encode("utf-8"))
        cid, did = campaign.id, draft.id
    async with db.unit_of_work() as s:
        await CanonImportService(s).approve(import_id=did, campaign_id=cid)
    return cid


# --- deterministic validation ---------------------------------------------------

def test_validation_flags_missing_start_unreachable_and_broken_refs():
    _, harbor, _ = parse_campaign_file("h.md", _HARBOR.encode("utf-8"))
    assert validate_campaign(harbor).ok                     # a sound campaign passes

    # Break it: add an unreachable island + a secret with no clue + drop the start.
    from app.services.campaigns.canon_import import CampaignProposal, LocationProposal
    broken = CampaignProposal(
        locations=[
            LocationProposal(key="a", name="A", obvious="x"),
            LocationProposal(key="island", name="Island", obvious="แยกเดี่ยว"),  # unreachable
        ],
        starting_location="a")
    res = validate_campaign(broken)
    kinds = {i.kind for i in res.issues}
    assert "unreachable" in kinds                           # island can't be reached
    assert res.errors                                       # blocks commit


def test_validation_flags_secret_without_clue_and_duplicate_identity():
    from app.services.campaigns.canon_import import (
        CampaignProposal, LocationProposal, NPCProposal, SecretProposal)
    p = CampaignProposal(
        locations=[LocationProposal(key="hall", name="Hall", obvious="x")],
        starting_location="hall",
        npcs=[NPCProposal(key="dup", name="A", goal="g", location="hall"),
              NPCProposal(key="dup", name="B", goal="g", location="hall")],  # duplicate key
        secrets=[SecretProposal(key="s", fact="hidden", clues=[])])          # no clue path
    res = validate_campaign(p)
    kinds = {i.kind for i in res.issues}
    assert "duplicate_identity" in kinds and "secret_no_clue" in kinds


# --- provenance priority --------------------------------------------------------

def test_explicit_owner_canon_outranks_all_ai_and_is_never_overwritten():
    assert outranks("IMPORTED_EXPLICIT", "AI_PROPOSED_CANON")
    assert outranks("IMPORTED_EXPLICIT", "AI_RUNTIME_EXPANDED")
    assert outranks("OWNER_EDITED", "AI_INFERRED_CONNECTOR")
    # AI content can never overwrite explicit owner canon.
    assert may_overwrite("IMPORTED_EXPLICIT", "AI_RUNTIME_EXPANDED") is False
    assert may_overwrite("OWNER_EDITED", "AI_PROPOSED_CANON") is False
    # Two owner-explicit facts about one thing are a contradiction — NOT auto-resolved.
    assert may_overwrite("IMPORTED_EXPLICIT", "OWNER_EDITED") is False
    # AI-over-AI: a stronger AI provenance may update a weaker one.
    assert may_overwrite("AI_RUNTIME_EXPANDED", "AI_PROPOSED_CANON") is True


async def test_imported_locations_carry_explicit_provenance(db, provider):
    cid = await _import(db, "chan-harbor", _HARBOR)
    async with db.session() as s:
        locs = (await s.execute(select(Location).where(Location.campaign_id == cid))).scalars().all()
        assert {l.name for l in locs} == {"ท่าเรือเก่า", "ถนนริมน้ำ"}
        from app.services.campaigns.provenance import is_owner_explicit
        assert all(is_owner_explicit(l.provenance) for l in locs)   # owner canon


# --- important canon unchanged after import + isolation -------------------------

async def test_important_canon_survives_import_verbatim(db, provider):
    cid = await _import(db, "chan-harbor", _HARBOR)
    async with db.session() as s:
        campaign = await s.get(Campaign, cid)
        assert "เรือทุกลำมาถึง" in campaign.brief            # brief unchanged
        assert "กักเรือทั้งเมือง" in campaign.central_question
        npc = (await s.execute(select(NPC).where(NPC.campaign_id == cid))).scalars().one()
        assert npc.name == "นายท่าเฒ่า"
        secret = (await s.execute(select(Secret).where(Secret.campaign_id == cid))).scalars().one()
        assert "ขายทางออกของเมือง" in secret.fact           # secret truth intact


async def test_two_campaigns_never_leak(db, provider):
    harbor = await _import(db, "chan-harbor", _HARBOR)
    orchard = await _import(db, "chan-orchard", _ORCHARD)
    async with db.session() as s:
        h_npcs = {n.name for n in (await s.execute(select(NPC).where(NPC.campaign_id == harbor))).scalars()}
        o_npcs = {n.name for n in (await s.execute(select(NPC).where(NPC.campaign_id == orchard))).scalars()}
        assert h_npcs == {"นายท่าเฒ่า"} and o_npcs == {"คนสวนตาบอด"}
        h_story = (await s.get(Campaign, harbor)).main_story
        o_story = (await s.get(Campaign, orchard)).main_story
        assert "เรือ" in h_story["dramatic_question"]
        assert "ความทรงจำ" in o_story["dramatic_question"]   # distinct main stories


# --- main-story continuity ------------------------------------------------------

async def test_main_story_seeded_from_import_with_leads_and_hidden_truth(db, provider):
    cid = await _import(db, "chan-harbor", _HARBOR)
    async with db.session() as s:
        story = (await s.get(Campaign, cid)).main_story
    assert story["dramatic_question"] and story["state"] == "opening"
    assert "ขายทางออกของเมือง" in story["hidden_truth"]      # the concealed answer
    assert story["leads"]                                    # actionable threads exist
    assert any(d["what"] == "น้ำที่สูงขึ้น" for d in story["deadlines"])
    assert any(g["key"] == "main" and g["status"] == "open" for g in story["goals"])


async def test_main_story_reacts_to_player_branches_and_survives_restart(tmp_path, provider):
    from app.db.session import Database

    url = f"sqlite+aiosqlite:///{(tmp_path / 'story.db').as_posix()}"
    first = Database(url, echo=False)
    await first.create_all()
    try:
        cid = await _import(first, "chan-harbor", _HARBOR)
        # Players do unexpected things across turns; the story records + reacts,
        # never railroads, never forgets.
        async with first.unit_of_work() as s:
            svc = MainStoryService(s)
            await svc.record_branch(cid, turn=3, summary="ผู้เล่นเผาสมุดท่าเรือทิ้ง")
            await svc.set_goal_status(cid, "main", "transformed")
            await svc.advance_state(cid, "midpoint")
            await svc.set_npc_state(cid, "old-harbormaster", "หนีออกจากเมือง")
    finally:
        await first.dispose()

    restarted = Database(url, echo=False)
    try:
        async with restarted.session() as s:
            story = (await s.get(Campaign, cid)).main_story
        assert story["state"] == "midpoint"                 # survived restart
        assert story["branches"][0]["summary"] == "ผู้เล่นเผาสมุดท่าเรือทิ้ง"
        assert any(g["key"] == "main" and g["status"] == "transformed"
                   for g in story["goals"])                 # story reacted, not reset
        assert story["npc_states"]["old-harbormaster"] == "หนีออกจากเมือง"
        assert story["hidden_truth"]                        # never lost
    finally:
        await restarted.dispose()


async def test_main_quest_stays_actionable_while_open_with_leads(db, provider):
    cid = await _import(db, "chan-harbor", _HARBOR)
    async with db.session() as s:
        assert await MainStoryService(s).is_main_quest_actionable(cid) is True
    # Completing the main goal closes the through-line.
    async with db.unit_of_work() as s:
        await MainStoryService(s).set_goal_status(cid, "main", "completed")
    async with db.session() as s:
        assert await MainStoryService(s).is_main_quest_actionable(cid) is False
