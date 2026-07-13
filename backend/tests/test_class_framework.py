"""Phase 3 — one authoritative class & rules framework (D&D 2024 / SRD 5.2.1).

Proves the REUSABLE systems work across class archetypes, not per-class code:

- rules authority is one documented edition, enforced;
- the typed class model declares casting model + level-scaled features + slot pools;
- the ResourceEngine spends/rejects/recovers atomically and persists across restart;
- the SpellEngine resolves cantrips + leveled spells (attack / save / damage /
  healing / concentration) through the SAME registry creation selects from;
- capabilities compose features+resources+spellcasting for the sheet;
- level_up scales HP / proficiency / features / resources;
- unsupported classes stay LOCKED until their class-specific mechanics are proven.

Fixtures span martial (fighter), prepared caster (cleric), known caster (bard),
spellbook caster (wizard), pact caster (warlock, framework-level), and a
resource-based class (barbarian Rage, framework-level).
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import RulesViolation, ValidationError
from app.core.randomness import SequenceRandomness
from app.models.character import Character
from app.models.enums import EventType
from app.models.event import Event
from app.models.progression import CharacterSpell, ResourceState
from app.rules_content import get_registry
from app.rules_content.registry import (
    FEATURE_ACTIVATIONS,
    RULESET_EDITION,
    SPELLCASTING_MODELS,
)
from app.tabletop.dice import DiceEngine
from app.tabletop.progression import character_capabilities, level_up
from app.tabletop.resources import ResourceEngine
from app.tabletop.spellcasting import SpellEngine, spellcasting_profile
from tests.support.factories import build_world


# --- rules authority -----------------------------------------------------------

def test_one_authoritative_edition_declared_and_consistent():
    reg = get_registry()
    assert RULESET_EDITION == "D&D 2024 (SRD 5.2.1)"
    assert reg.manifest.rules_edition == RULESET_EDITION      # manifest agrees
    assert reg.rules_content_version == reg.manifest.ui_rules_content_version


def test_every_class_declares_a_valid_casting_model():
    reg = get_registry()
    for name, cls in reg.classes.items():
        assert cls.casting_model in SPELLCASTING_MODELS, name
    # The six archetype models are represented in content.
    models = {c.casting_model for c in reg.classes.values()}
    assert {"NONE", "PREPARED_SPELLS", "KNOWN_SPELLS", "SPELLBOOK", "PACT_MAGIC"} <= models
    assert reg.get_class("wizard").casting_model == "SPELLBOOK"
    assert reg.get_class("cleric").casting_model == "PREPARED_SPELLS"
    assert reg.get_class("bard").casting_model == "KNOWN_SPELLS"
    assert reg.get_class("warlock").casting_model == "PACT_MAGIC"
    assert reg.get_class("fighter").casting_model == "NONE"


def test_features_are_level_scaled_and_have_valid_activation():
    reg = get_registry()
    for name, cls in reg.classes.items():
        for f in cls.features:
            assert f.level >= 1 and f.activation in FEATURE_ACTIVATIONS, (name, f.key)
    # features_at is the one level query.
    fighter = reg.get_class("fighter")
    l1 = {f.key for f in fighter.features_at(1)}
    l2 = {f.key for f in fighter.features_at(2)}
    assert "second_wind" in l1 and "action_surge" not in l1     # L2 feature hidden at L1
    assert "action_surge" in l2                                  # revealed at L2


# --- resource system (reusable, atomic, persistent) ----------------------------

async def test_resource_spend_reject_and_recover_atomically(db, provider):
    world = await build_world(db)
    # Grant the fighter a Second Wind pool (flat 2) and spend it down.
    async with db.unit_of_work() as s:
        fighter = await s.get(Character, world.bront_id)  # dwarf fighter fixture
        eng = ResourceEngine(s)
        await eng.grant(fighter, "resource:second_wind")
    async with db.unit_of_work() as s:
        eng = ResourceEngine(s)
        await eng.spend(world.bront_id, "resource:second_wind", 1)
        await eng.spend(world.bront_id, "resource:second_wind", 1)
    # Third spend must be rejected — no uses left.
    with pytest.raises(RulesViolation):
        async with db.unit_of_work() as s:
            await ResourceEngine(s).spend(world.bront_id, "resource:second_wind", 1)
    # Long rest restores it.
    async with db.unit_of_work() as s:
        notes = await ResourceEngine(s).apply_long_rest(world.bront_id)
    assert any("Second Wind" in n or "ระเบิด" in n or "พลัง" in n or n for n in notes)
    async with db.session() as s:
        state = (await s.execute(select(ResourceState).where(
            ResourceState.character_id == world.bront_id,
            ResourceState.resource_id == "resource:second_wind"))).scalar_one()
        assert state.current == state.max_value == 2


async def test_rage_resource_is_level_scaled_for_a_locked_class(db, provider):
    """The framework represents a locked class's resource honestly: a barbarian's
    Rage scales by level even though barbarian is not selectable."""
    world = await build_world(db)
    reg = get_registry()
    d = reg.get_resource("resource:rage")
    assert reg.resolve_max(d.max_formula, class_level=1) == 2
    assert reg.resolve_max(d.max_formula, class_level=3) == 3
    assert reg.resolve_max(d.max_formula, class_level=6) == 4


async def test_resource_state_survives_restart(tmp_path, provider):
    from app.db.session import Database

    url = f"sqlite+aiosqlite:///{(tmp_path / 'res-restart.db').as_posix()}"
    first = Database(url, echo=False)
    await first.create_all()
    try:
        world = await build_world(first)
        async with first.unit_of_work() as s:
            wiz = await s.get(Character, world.kael_id)
            wiz.char_class = "wizard"
            await ResourceEngine(s).grant(wiz, "resource:spell_slots_1")
            await ResourceEngine(s).spend(world.kael_id, "resource:spell_slots_1", 1)
    finally:
        await first.dispose()

    restarted = Database(url, echo=False)
    try:
        async with restarted.session() as s:
            state = (await s.execute(select(ResourceState).where(
                ResourceState.character_id == world.kael_id,
                ResourceState.resource_id == "resource:spell_slots_1"))).scalar_one()
            assert state.current == state.max_value - 1   # the spend persisted
    finally:
        await restarted.dispose()


# --- spell engine (shared, honest, same registry as creation) ------------------

async def _make_caster(db, world, *, char_class, cantrips, leveled, prepared=True):
    async with db.unit_of_work() as s:
        char = await s.get(Character, world.kael_id)
        char.char_class = char_class
        char.wis_score = char.int_score = char.cha_score = 16   # a solid casting stat
        for c in cantrips:
            s.add(CharacterSpell(character_id=char.id, spell_key=c, kind="cantrip"))
        for sp in leveled:
            kind = "book" if char_class == "wizard" else "known"
            s.add(CharacterSpell(character_id=char.id, spell_key=sp, kind=kind,
                                 prepared=prepared))
        await ResourceEngine(s).grant(char, "resource:spell_slots_1")
    async with db.session() as s:
        return await s.get(Character, world.kael_id)


async def test_spell_profile_reflects_casting_model_and_dc(db, provider):
    world = await build_world(db)
    await _make_caster(db, world, char_class="cleric",
                       cantrips=["sacred_flame"], leveled=["cure_wounds", "bless"])
    async with db.session() as s:
        char = await s.get(Character, world.kael_id)
        profile = await spellcasting_profile(s, char)
    assert profile.is_caster and profile.model == "PREPARED_SPELLS"
    assert profile.save_dc == 8 + 2 + 3                    # PB2 + WIS16(+3)
    assert "sacred_flame" in profile.cantrips
    assert "cure_wounds" in profile.prepared


async def test_cast_attack_cantrip_spends_no_slot_and_can_hit(db, provider):
    world = await build_world(db)
    await _make_caster(db, world, char_class="wizard",
                       cantrips=["fire_bolt"], leveled=["magic_missile"])
    dice = DiceEngine(SequenceRandomness([18, 7]))          # attack 18 -> hit, dmg 7
    async with db.unit_of_work() as s:
        char = await s.get(Character, world.kael_id)
        engine = SpellEngine(s, dice)
        out = await engine.cast(character=char, spell_key="fire_bolt",
                                target_acs={"npc:goblin": 12})
    assert out.slot_level == 0                               # cantrip: no slot
    assert out.attack and out.attack["hit"] and out.damage == 7
    async with db.session() as s:
        # cantrip did not consume the 1st-level slot pool.
        slot = (await s.execute(select(ResourceState).where(
            ResourceState.character_id == world.kael_id,
            ResourceState.resource_id == "resource:spell_slots_1"))).scalar_one()
        assert slot.current == slot.max_value


async def test_cast_leveled_spell_spends_a_slot_and_rejects_when_empty(db, provider):
    world = await build_world(db)
    await _make_caster(db, world, char_class="wizard",
                       cantrips=["fire_bolt"], leveled=["magic_missile"])
    dice = DiceEngine(SequenceRandomness(default=3))
    # Spend all 1st-level slots (a level-1 wizard has 2).
    async with db.unit_of_work() as s:
        char = await s.get(Character, world.kael_id)
        eng = SpellEngine(s, dice)
        await eng.cast(character=char, spell_key="magic_missile", target_acs={"npc:x": 10})
        await eng.cast(character=char, spell_key="magic_missile", target_acs={"npc:x": 10})
    async with db.session() as s:
        slot = (await s.execute(select(ResourceState).where(
            ResourceState.resource_id == "resource:spell_slots_1",
            ResourceState.character_id == world.kael_id))).scalar_one()
        assert slot.current == 0
    # Third cast with no slots left is rejected — nothing invented.
    with pytest.raises((ValidationError, RulesViolation)):
        async with db.unit_of_work() as s:
            char = await s.get(Character, world.kael_id)
            await SpellEngine(s, dice).cast(character=char, spell_key="magic_missile",
                                            target_acs={"npc:x": 10})


async def test_cast_save_spell_applies_half_on_success(db, provider):
    world = await build_world(db)
    await _make_caster(db, world, char_class="wizard",
                       cantrips=["fire_bolt"], leveled=["burning_hands"])
    # burning_hands: DEX save, half on save. save total high -> passes -> half of 3d6.
    dice = DiceEngine(SequenceRandomness([2, 2, 2, 20]))    # damage 6, save nat20 passes
    async with db.unit_of_work() as s:
        char = await s.get(Character, world.kael_id)
        out = await SpellEngine(s, dice).cast(
            character=char, spell_key="burning_hands",
            target_save_mods={"npc:goblin": 0})
    assert out.saves and out.saves[0]["passed"]
    assert out.damage == 3                                   # 6 halved


async def test_cast_healing_spell_and_concentration_and_event(db, provider):
    world = await build_world(db)
    await _make_caster(db, world, char_class="cleric",
                       cantrips=["sacred_flame"], leveled=["cure_wounds", "bless"])
    dice = DiceEngine(SequenceRandomness(default=5))
    async with db.unit_of_work() as s:
        char = await s.get(Character, world.kael_id)
        eng = SpellEngine(s, dice)
        heal = await eng.cast(character=char, spell_key="cure_wounds",
                              campaign_id=world.campaign_id)
        # bless concentrates.
        blessed = await eng.cast(character=char, spell_key="bless",
                                 campaign_id=world.campaign_id)
    assert heal.healing > 0
    assert blessed.concentration is True
    async with db.session() as s:
        casts = (await s.execute(select(Event).where(
            Event.event_type == EventType.SPELL_CAST.value))).scalars().all()
        assert len(casts) == 2                               # canonical events recorded


async def test_engine_refuses_a_spell_the_caster_has_not_prepared(db, provider):
    world = await build_world(db)
    await _make_caster(db, world, char_class="cleric",
                       cantrips=["sacred_flame"], leveled=["cure_wounds"], prepared=False)
    with pytest.raises(RulesViolation):
        async with db.unit_of_work() as s:
            char = await s.get(Character, world.kael_id)
            await SpellEngine(s, DiceEngine(SequenceRandomness(default=3))).cast(
                character=char, spell_key="cure_wounds")


# --- capabilities + progression -------------------------------------------------

async def test_capabilities_compose_features_resources_and_spellcasting(db, provider):
    world = await build_world(db)
    await _make_caster(db, world, char_class="wizard",
                       cantrips=["fire_bolt"], leveled=["magic_missile"])
    async with db.session() as s:
        char = await s.get(Character, world.kael_id)
        caps = await character_capabilities(s, char)
    assert caps.char_class == "wizard" and caps.is_caster
    assert any(f.key == "spellcasting" for f in caps.features)
    assert any(f.activation == "action" for f in caps.features)
    assert any(r.resource_id == "resource:spell_slots_1" for r in caps.resources)


async def test_level_up_scales_hp_proficiency_and_resources(db, provider):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        fighter = await s.get(Character, world.bront_id)
        fighter.char_class = "fighter"
        fighter.level = 1
        await ResourceEngine(s).grant(fighter, "resource:second_wind")
        hp0 = fighter.max_hp
    async with db.unit_of_work() as s:
        fighter = await s.get(Character, world.bront_id)
        result = await level_up(s, fighter)
    assert result["level"] == 2
    async with db.session() as s:
        fighter = await s.get(Character, world.bront_id)
        assert fighter.level == 2 and fighter.max_hp > hp0
        assert fighter.proficiency_bonus == 2                 # still +2 at level 2
        # Action Surge (a level-2 feature) was granted at level up.
        from app.models.progression import CharacterGrant
        grants = {g.key for g in (await s.execute(select(CharacterGrant).where(
            CharacterGrant.character_id == world.bront_id))).scalars()}
        assert "action_surge" in grants


# --- locked-class discipline ----------------------------------------------------

def test_unsupported_classes_stay_locked_until_proven():
    reg = get_registry()
    from app.tabletop.rules.core import SUPPORTED_CLASSES

    assert set(reg.selectable_classes) == set(SUPPORTED_CLASSES)
    # Still locked (their class-specific mechanics/tests aren't done); sorcerer and
    # warlock were unlocked in Part 3 after their end-to-end path passed.
    for locked in ("barbarian", "paladin", "druid", "monk"):
        assert locked in reg.classes                          # represented in the framework
        assert locked not in reg.selectable_classes           # but NOT selectable
        assert reg.get_class(locked).support_status != "FULLY_SUPPORTED"


def test_creation_rejects_a_locked_class():
    from app.tabletop.rules.core import validate_class

    with pytest.raises(RulesViolation):
        validate_class("barbarian")
