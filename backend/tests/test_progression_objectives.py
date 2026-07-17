"""The objective layer: campaign goal → chapter goal → objective → task.

Reverie had the top of this hierarchy (Campaign.central_question) and the bottom
(Scene.purpose) and nothing between them, so nothing could answer "what is the party
supposed to be doing right now" (docs/progression-audit.md, RC2).

The load-bearing rule these tests pin: a chapter advances on RESOLUTION, not on
success. A chapter that waits for success is one failed check away from a permanent
deadlock — the "failed check blocks the only route forward" symptom.
"""
from __future__ import annotations

from sqlalchemy import select

from app.memory.progression_context import ProgressionContextBuilder
from app.models.campaign_progression import Chapter
from app.models.consequences import Quest
from app.services.campaigns import CampaignService
from app.services.campaigns.canon_import import CanonImportService
from app.services.campaigns.progression_service import ProgressionService

_CAMPAIGN = """# Campaign: เมืองท่าที่เรือไม่ออก

## Central Question
ใครหรืออะไรกักเรือทั้งเมืองไว้

## Chapter: ท่าเรือที่เงียบผิดปกติ
### key
ch-harbor
### goal
หาว่าทำไมเรือถึงออกไม่ได้
### order
1

## Chapter: สิ่งที่อยู่ใต้น้ำ
### key
ch-deep
### goal
เผชิญหน้ากับสิ่งที่ทำสัญญาไว้
### order
2

## Objective: สมุดท่าเรือ
### key
obj-ledger
### task
อ่านหน้าที่ถูกฉีกในสมุดท่าเรือ
### chapter
ch-harbor
### order
1

## Objective: ถามนายท่า
### key
obj-harbormaster
### task
ถามนายท่าเฒ่าว่าคืนนั้นเขาเห็นอะไร
### chapter
ch-harbor
### order
2

## Objective: ตำนานชาวเรือ
### key
obj-sailor-tale
### task
ฟังเรื่องเล่าของชาวเรือขี้เมา
### chapter
ch-harbor
### order
3
### optional

## Location: ท่าเรือเก่า
### key
old-harbor
### obvious
ท่าเรือหินที่เรือจอดแน่นขนัด
"""


async def _import(db, channel: str, text: str = _CAMPAIGN) -> str:
    async with db.unit_of_work() as s:
        camp = CampaignService(s)
        campaign = await camp.create_campaign(
            name="obj", discord_guild_id="g", game_channel_id=channel,
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


# --- import produces a hierarchy the engine can operate ------------------------

async def test_import_creates_chapters_and_objectives(db, provider):
    cid = await _import(db, "chan-obj")
    async with db.session() as s:
        chapters = (await s.execute(
            select(Chapter).where(Chapter.campaign_id == cid)
            .order_by(Chapter.sort_order))).scalars().all()
        quests = (await s.execute(
            select(Quest).where(Quest.campaign_id == cid))).scalars().all()
    assert [c.key for c in chapters] == ["ch-harbor", "ch-deep"]
    assert {q.key for q in quests} == {"obj-ledger", "obj-harbormaster", "obj-sailor-tale"}
    # Objectives are linked to their chapter, not free-floating.
    by_key = {q.key: q for q in quests}
    assert by_key["obj-ledger"].chapter_id == chapters[0].id
    assert by_key["obj-sailor-tale"].optional is True
    assert by_key["obj-ledger"].optional is False, "a bare `### optional` must not leak"


async def test_imported_campaign_opens_with_an_active_chapter_and_a_task(db, provider):
    """The 'dropped in without a clear immediate objective' symptom: a party must
    arrive with chapter one open and a concrete task, not just a goal."""
    cid = await _import(db, "chan-open")
    async with db.session() as s:
        chapter = await ProgressionService(s).active_chapter(cid)
        ctx = await ProgressionContextBuilder(s).build(campaign_id=cid)
    assert chapter is not None and chapter.key == "ch-harbor"
    assert ctx.chapter_goal == "หาว่าทำไมเรือถึงออกไม่ได้"
    assert ctx.active_objective == "อ่านหน้าที่ถูกฉีกในสมุดท่าเรือ"   # the immediate task
    assert "ถามนายท่าเฒ่าว่าคืนนั้นเขาเห็นอะไร" in ctx.leads          # the other opportunities
    block = ctx.as_block()
    assert "CHAPTER:" in block and "OBJECTIVE:" in block


async def test_objectives_of_unopened_chapters_are_not_offered(db, provider):
    """Chapter two's work must not be presented while chapter one is active — that
    points the party at something the campaign has not opened."""
    cid = await _import(db, "chan-scope")
    async with db.unit_of_work() as s:
        ch2 = (await s.execute(select(Chapter).where(
            Chapter.campaign_id == cid, Chapter.key == "ch-deep"))).scalars().one()
        s.add(Quest(campaign_id=cid, key="obj-dive", name="ดำลงไป",
                    task="ดำลงไปใต้ท่าเรือ", chapter_id=ch2.id, state="DISCOVERED"))
    async with db.session() as s:
        objectives = await ProgressionService(s).active_objectives(cid)
    assert "obj-dive" not in {q.key for q in objectives}


# --- the deadlock rule ---------------------------------------------------------

async def test_chapter_advances_when_required_objectives_resolve(db, provider):
    cid = await _import(db, "chan-advance")
    async with db.unit_of_work() as s:
        svc = ProgressionService(s)
        for key in ("obj-ledger", "obj-harbormaster"):
            q = (await s.execute(select(Quest).where(
                Quest.campaign_id == cid, Quest.key == key))).scalars().one()
            q.state = "COMPLETED"
        advance = await svc.advance_chapter_if_resolved(cid)
    assert advance.completed_chapter_key == "ch-harbor"
    assert advance.opened_chapter_key == "ch-deep"
    async with db.session() as s:
        assert (await ProgressionService(s).active_chapter(cid)).key == "ch-deep"


async def test_a_failed_objective_does_not_deadlock_the_campaign(db, provider):
    """The core rule. A failed check resolves an objective — the story moves on
    changed. If chapters waited for success, one bad roll would strand the party in
    chapter one forever with no route forward.
    """
    cid = await _import(db, "chan-failed")
    async with db.unit_of_work() as s:
        for key, state in (("obj-ledger", "FAILED"), ("obj-harbormaster", "COMPLETED")):
            q = (await s.execute(select(Quest).where(
                Quest.campaign_id == cid, Quest.key == key))).scalars().one()
            q.state = state
        advance = await ProgressionService(s).advance_chapter_if_resolved(cid)
    assert advance.moved, "a FAILED objective is RESOLVED — the chapter must still advance"
    assert advance.opened_chapter_key == "ch-deep"


async def test_an_optional_objective_never_gates_a_chapter(db, provider):
    """A missable thread must not strand the campaign."""
    cid = await _import(db, "chan-optional")
    async with db.unit_of_work() as s:
        for key in ("obj-ledger", "obj-harbormaster"):
            q = (await s.execute(select(Quest).where(
                Quest.campaign_id == cid, Quest.key == key))).scalars().one()
            q.state = "COMPLETED"
        # obj-sailor-tale stays UNKNOWN/DISCOVERED — never touched.
        advance = await ProgressionService(s).advance_chapter_if_resolved(cid)
    assert advance.moved


async def test_chapter_does_not_advance_while_a_required_objective_is_open(db, provider):
    cid = await _import(db, "chan-hold")
    async with db.unit_of_work() as s:
        q = (await s.execute(select(Quest).where(
            Quest.campaign_id == cid, Quest.key == "obj-ledger"))).scalars().one()
        q.state = "COMPLETED"
        advance = await ProgressionService(s).advance_chapter_if_resolved(cid)
    assert not advance.moved
    async with db.session() as s:
        assert (await ProgressionService(s).active_chapter(cid)).key == "ch-harbor"


async def test_final_chapter_completion_finishes_the_campaign(db, provider):
    cid = await _import(db, "chan-finish")
    async with db.unit_of_work() as s:
        ch2 = (await s.execute(select(Chapter).where(
            Chapter.campaign_id == cid, Chapter.key == "ch-deep"))).scalars().one()
        s.add(Quest(campaign_id=cid, key="obj-confront", name="เผชิญหน้า",
                    chapter_id=ch2.id, state="COMPLETED"))
        for key in ("obj-ledger", "obj-harbormaster"):
            q = (await s.execute(select(Quest).where(
                Quest.campaign_id == cid, Quest.key == key))).scalars().one()
            q.state = "COMPLETED"
        await ProgressionService(s).advance_chapter_if_resolved(cid)
        final = await ProgressionService(s).advance_chapter_if_resolved(cid)
    assert final.completed_chapter_key == "ch-deep"
    assert final.campaign_finished is True
    assert final.opened_chapter_key == ""


async def test_starting_the_first_chapter_is_idempotent(db, provider):
    """Re-running setup must never yank an in-progress party back to chapter one."""
    cid = await _import(db, "chan-idem")
    async with db.unit_of_work() as s:
        for key in ("obj-ledger", "obj-harbormaster"):
            q = (await s.execute(select(Quest).where(
                Quest.campaign_id == cid, Quest.key == key))).scalars().one()
            q.state = "COMPLETED"
        await ProgressionService(s).advance_chapter_if_resolved(cid)   # now on ch-deep
    async with db.unit_of_work() as s:
        again = await ProgressionService(s).start_first_chapter(cid)
    assert again is None
    async with db.session() as s:
        assert (await ProgressionService(s).active_chapter(cid)).key == "ch-deep"


async def test_opening_a_chapter_discovers_its_objectives(db, provider):
    cid = await _import(db, "chan-discover")
    async with db.session() as s:
        quests = (await s.execute(select(Quest).where(
            Quest.campaign_id == cid))).scalars().all()
    # Chapter one opened at import, so its objectives are known work now.
    assert all(q.state == "DISCOVERED" for q in quests), \
        "an opened chapter's objectives must become DISCOVERED"
