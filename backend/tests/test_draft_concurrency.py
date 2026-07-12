"""Draft writes are DATABASE-arbitrated, not just lock-arbitrated.

The creation flow's asyncio lock serializes one process. These tests prove the
row itself rejects what the lock cannot see: a second process (or a
double-delivered Discord interaction on another worker) writing over a draft it
read before someone else changed it, two ACTIVE drafts for one member, or a
draft finalized twice into two characters.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.character import Character
from app.models.character_draft import CharacterDraft
from app.services.campaigns.draft_store import DraftConflict, close_draft, save_draft
from tests.support.factories import build_world


async def _make_draft(db, world, data=None) -> CharacterDraft:
    async with db.unit_of_work() as s:
        draft = CharacterDraft(
            campaign_id=world.campaign_id, member_id=world.p1_member_id,
            data=data or {"_last_prompt": "เล่ามา"},
        )
        s.add(draft)
        await s.flush()
        draft_id = draft.id
    async with db.session() as s:
        return (await s.execute(
            select(CharacterDraft).where(CharacterDraft.id == draft_id))).scalar_one()


async def test_concurrent_saves_cannot_silently_overwrite(db, provider):
    """Two writers read the same version; the first save wins, the second gets a
    conflict instead of clobbering the winner's selections."""
    world = await build_world(db)
    draft = await _make_draft(db, world)

    # Both writers hold the same snapshot (version 0).
    async with db.session() as s:
        writer_a = (await s.execute(
            select(CharacterDraft).where(CharacterDraft.id == draft.id))).scalar_one()
    async with db.session() as s:
        writer_b = (await s.execute(
            select(CharacterDraft).where(CharacterDraft.id == draft.id))).scalar_one()

    await save_draft(db, writer_a, {"concept": "จากหมาป่า A"})
    with pytest.raises(DraftConflict):
        await save_draft(db, writer_b, {"concept": "จากหมาป่า B"})

    async with db.session() as s:
        row = (await s.execute(
            select(CharacterDraft).where(CharacterDraft.id == draft.id))).scalar_one()
        assert row.data == {"concept": "จากหมาป่า A"}   # A's write intact, B rejected
        assert row.version == 1


async def test_winner_can_keep_saving_after_its_own_write(db, provider):
    """save_draft advances the in-memory version so multi-save turns keep working."""
    world = await build_world(db)
    draft = await _make_draft(db, world)
    await save_draft(db, draft, {"step": "one"})
    await save_draft(db, draft, {"step": "two"})        # would conflict if stale
    async with db.session() as s:
        row = (await s.execute(
            select(CharacterDraft).where(CharacterDraft.id == draft.id))).scalar_one()
        assert row.data == {"step": "two"} and row.version == 2


async def test_save_after_cancel_is_rejected(db, provider):
    world = await build_world(db)
    draft = await _make_draft(db, world)
    assert await close_draft(db, draft.id, status="CANCELLED") is True
    assert await close_draft(db, draft.id, status="CANCELLED") is False   # idempotent
    with pytest.raises(DraftConflict):
        await save_draft(db, draft, {"concept": "หลังยกเลิก"})


async def test_second_active_draft_for_same_member_is_impossible(db, provider):
    from sqlalchemy.exc import IntegrityError

    world = await build_world(db)
    await _make_draft(db, world)
    with pytest.raises(IntegrityError):
        await _make_draft(db, world)
    # A CLOSED draft does not block starting fresh.
    async with db.session() as s:
        first = (await s.execute(select(CharacterDraft))).scalars().first()
    await close_draft(db, first.id, status="CANCELLED")
    replacement = await _make_draft(db, world)
    assert replacement.status == "ACTIVE"


async def test_double_finalize_creates_exactly_one_character(db, provider):
    """The finalize transaction claims the draft ACTIVE->DONE first; a duplicate
    finalize (double-delivered confirm on another worker) creates NO second
    character and gets a calm already-done notice."""
    from app.services.campaigns.finalize import finalize_character

    world = await build_world(db)
    build = {
        "step": "review", "class": "rogue", "species": "human", "background": "criminal",
        "scores": {"str": 8, "dex": 17, "con": 13, "int": 12, "wis": 14, "cha": 10},
        "skills": ["stealth", "acrobatics", "perception", "investigation"],
        "species_skill:skillful": "insight",
        "expertise": ["stealth", "perception"],
        "component_token": "tok",
    }
    draft = await _make_draft(db, world, data={"name": "Nara", "_build": build})
    data = dict(draft.data)

    async with db.session() as s:
        before = len((await s.execute(select(Character))).scalars().all())

    r1 = await finalize_character(db, draft=draft, data=data, channel_id="chan-1")
    assert r1.state_mutated is True
    r2 = await finalize_character(db, draft=draft, data=data, channel_id="chan-1")
    assert r2.state_mutated is False
    assert "ถูกสร้างเสร็จไปแล้ว" in r2.responses[0].content

    async with db.session() as s:
        after = (await s.execute(select(Character))).scalars().all()
        named = [c for c in after if c.name == "Nara"]
        assert len(after) == before + 1 and len(named) == 1
