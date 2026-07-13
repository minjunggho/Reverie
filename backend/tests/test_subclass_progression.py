"""Part 2 — subclass selection, activation, feature grants, persistence.

`planned_subclass` (creation) is NEVER mechanical; only `active_subclass` — set at
the class's authoritative subclass level after confirmation — grants features. Uses
the shared CharacterGrant/ResourceEngine/CharacterSpell systems; idempotent.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import RulesViolation
from app.models.character import Character
from app.models.progression import CharacterGrant, CharacterSpell
from app.rules_content import get_registry
from app.tabletop.progression import (
    SubclassSelectionRequired,
    SubclassService,
    level_up,
)
from tests.support.factories import build_world


async def _at(db, char_id, char_class, level, *, active_subclass=None, planned=None):
    async with db.unit_of_work() as s:
        c = await s.get(Character, char_id)
        c.char_class = char_class
        c.level = level
        c.active_subclass = active_subclass
        c.planned_subclass = planned
    async with db.session() as s:
        return await s.get(Character, char_id)


# --- registry audit: every class + its authoritative subclass level -------------

def test_subclass_selection_level_read_per_class_never_assumed():
    reg = get_registry()
    classes = ("fighter", "rogue", "barbarian", "monk", "cleric", "druid",
               "paladin", "ranger", "bard", "sorcerer", "warlock", "wizard")
    svc = SubclassService.__new__(SubclassService)
    svc.reg = reg
    for name in classes:
        cls = reg.get_class(name)
        assert cls.subclass_level >= 1                        # declared, not assumed
        subs = reg.subclasses_for_class(name)
        assert len(subs) >= 1                                 # every class has options
        for sub in subs:
            assert sub.parent_class == name


# --- planned vs active ----------------------------------------------------------

async def test_planned_subclass_grants_no_mechanics(db, provider):
    world = await build_world(db)
    # A rogue with a PLANNED subclass at level 1 (rogue chooses at level 3).
    await _at(db, world.kael_id, "rogue", 1, planned="thief")
    async with db.session() as s:
        c = await s.get(Character, world.kael_id)
        assert c.planned_subclass == "thief"
        assert c.active_subclass is None                      # not mechanical
        grants = {g.key for g in (await s.execute(select(CharacterGrant).where(
            CharacterGrant.character_id == c.id,
            CharacterGrant.grant_type == "subclass_feature"))).scalars()}
        assert grants == set()                                # no subclass features granted


async def test_requires_selection_only_at_the_subclass_level(db, provider):
    world = await build_world(db)
    svc = SubclassService.__new__(SubclassService); svc.reg = get_registry()
    c1 = await _at(db, world.kael_id, "fighter", 2)
    assert svc.requires_selection(c1) is False               # below level 3
    c3 = await _at(db, world.kael_id, "fighter", 3)
    assert svc.requires_selection(c3) is True                # AT level 3, no active


# --- level-up gate: does not complete without the required choice ---------------

async def test_level_up_to_subclass_level_requires_a_choice(db, provider):
    world = await build_world(db)
    await _at(db, world.kael_id, "fighter", 2)
    # Level 2 -> 3 is the fighter subclass level: level-up must PAUSE for a choice.
    with pytest.raises(SubclassSelectionRequired):
        async with db.unit_of_work() as s:
            c = await s.get(Character, world.kael_id)
            await level_up(s, c)                              # no chosen_subclass
    async with db.session() as s:
        c = await s.get(Character, world.kael_id)
        assert c.active_subclass is None                      # unresolved, not silently set


async def test_level_up_with_choice_activates_and_grants_features_once(db, provider):
    world = await build_world(db)
    await _at(db, world.kael_id, "fighter", 2)
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        result = await level_up(s, c, chosen_subclass="champion")
    assert result["level"] == 3
    async with db.session() as s:
        c = await s.get(Character, world.kael_id)
        assert c.active_subclass == "champion"
        feats = [g.key for g in (await s.execute(select(CharacterGrant).where(
            CharacterGrant.character_id == c.id,
            CharacterGrant.grant_type == "subclass_feature"))).scalars()]
        assert feats == ["improved_critical"]                 # granted exactly once


async def test_subclass_spell_grants_land_once(db, provider):
    world = await build_world(db)
    await _at(db, world.kael_id, "cleric", 2, active_subclass=None)
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        await level_up(s, c, chosen_subclass="life_domain")   # grants cure_wounds, bless
    # A second level-up must not duplicate the always-prepared subclass spells.
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        await level_up(s, c)                                  # level 3 -> 4
    async with db.session() as s:
        spells = [r.spell_key for r in (await s.execute(select(CharacterSpell).where(
            CharacterSpell.character_id == world.kael_id,
            CharacterSpell.source_type == "SUBCLASS"))).scalars()]
        assert sorted(spells) == ["bless", "cure_wounds"]     # once each


async def test_double_confirmation_grants_once(db, provider):
    world = await build_world(db)
    c = await _at(db, world.kael_id, "rogue", 3)
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        await SubclassService(s).select_subclass(c, "thief")
    async with db.unit_of_work() as s:                        # a duplicate confirm
        c = await s.get(Character, world.kael_id)
        await SubclassService(s).select_subclass(c, "thief")
    async with db.session() as s:
        feats = [g.key for g in (await s.execute(select(CharacterGrant).where(
            CharacterGrant.character_id == world.kael_id,
            CharacterGrant.grant_type == "subclass_feature"))).scalars()]
        assert feats == ["fast_hands"]                        # not doubled


# --- validation -----------------------------------------------------------------

async def test_invalid_and_foreign_subclasses_rejected(db, provider):
    world = await build_world(db)
    c = await _at(db, world.kael_id, "fighter", 3)
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        svc = SubclassService(s)
        with pytest.raises(RulesViolation):
            await svc.select_subclass(c, "not_a_subclass")     # unknown
        with pytest.raises(RulesViolation):
            await svc.select_subclass(c, "thief")              # rogue subclass on a fighter


async def test_below_level_selection_rejected(db, provider):
    world = await build_world(db)
    c = await _at(db, world.kael_id, "fighter", 2)             # below subclass level
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        with pytest.raises(RulesViolation):
            await SubclassService(s).select_subclass(c, "champion")


# --- persistence / restart / sheet ----------------------------------------------

async def test_active_subclass_and_features_survive_restart(tmp_path, provider):
    from app.db.session import Database

    url = f"sqlite+aiosqlite:///{(tmp_path / 'subclass-restart.db').as_posix()}"
    first = Database(url, echo=False)
    await first.create_all()
    try:
        world = await build_world(first)
        await _at(first, world.kael_id, "rogue", 3)
        async with first.unit_of_work() as s:
            c = await s.get(Character, world.kael_id)
            await SubclassService(s).select_subclass(c, "thief")
    finally:
        await first.dispose()

    restarted = Database(url, echo=False)
    try:
        async with restarted.session() as s:
            c = await s.get(Character, world.kael_id)
            assert c.active_subclass == "thief"               # persisted
            feats = await SubclassService(s).subclass_features(c)
            assert [f.key for f in feats] == ["fast_hands"]    # visible on the sheet
    finally:
        await restarted.dispose()


async def test_existing_character_without_subclass_data_is_safe(db, provider):
    """A pre-migration character (no subclass at all) behaves: planned/active both
    NULL, no features, requires_selection only once it hits the level."""
    world = await build_world(db)
    c = await _at(db, world.kael_id, "wizard", 1)
    async with db.session() as s:
        c = await s.get(Character, world.kael_id)
        assert c.active_subclass is None and c.planned_subclass is None
        assert SubclassService(s).requires_selection(c) is False
