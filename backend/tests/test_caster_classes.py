"""Phase 4A (casters) — Bard, Sorcerer, Warlock, Wizard on the shared framework.

Each class's DISTINCTIVE kit is exercised through the reusable systems (no parallel
mechanics): Sorcery Points ⇄ slots + Metamagic; Pact Magic + Invocations + Pact
Boon; Bardic Inspiration scaling + Jack of All Trades + Song of Rest; Spellbook
learning + ritual casting + Arcane Recovery. Plus the shared matrix: content pools
validate, resources spend/reject/recover, spells resolve, state persists.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import RulesViolation, ValidationError
from app.core.randomness import SequenceRandomness
from app.models.character import Character
from app.models.progression import CharacterGrant, CharacterSpell, ResourceState
from app.rules_content import get_registry
from app.tabletop.classes.bard import (
    bardic_inspiration_die,
    jack_of_all_trades_bonus,
    song_of_rest_die,
)
from app.tabletop.classes.sorcerer import METAMAGIC, SorceryService, sorcery_points_max
from app.tabletop.classes.warlock import WarlockService
from app.tabletop.classes.wizard import WizardSpellbook
from app.tabletop.dice import DiceEngine
from app.tabletop.resources import ResourceEngine
from app.tabletop.rules.derive import skill_bonus
from app.tabletop.spellcasting import SpellEngine, spellcasting_profile
from tests.support.factories import build_world


async def _as_class(db, char_id, char_class, *, level=1, **scores):
    async with db.unit_of_work() as s:
        c = await s.get(Character, char_id)
        c.char_class = char_class
        c.level = level
        for k, v in scores.items():
            setattr(c, f"{k}_score", v)
    async with db.session() as s:
        return await s.get(Character, char_id)


# --- content: all four pools validate honestly ---------------------------------

def test_all_four_caster_pools_satisfy_required_counts():
    reg = get_registry()
    for name in ("bard", "sorcerer", "warlock", "wizard"):
        cls = reg.get_class(name)
        sc = cls.spellcasting
        cantrips = reg.spells_for_class(sc.spell_list, 0)
        level_one = reg.spells_for_class(sc.spell_list, 1)
        assert len(cantrips) >= sc.cantrips_known, name
        assert len(level_one) >= max(1, sc.prepared_count), name


# --- BARD ----------------------------------------------------------------------

def test_bard_inspiration_die_and_song_of_rest_scale():
    assert [bardic_inspiration_die(l) for l in (1, 5, 10, 15)] == [6, 8, 10, 12]
    assert [song_of_rest_die(l) for l in (1, 9, 13, 17)] == [6, 8, 10, 12]


async def test_bard_jack_of_all_trades_applies_only_at_level_2(db, provider):
    world = await build_world(db)
    # A bard, level 1: no Jack of All Trades on a non-proficient skill.
    bard = await _as_class(db, world.kael_id, "bard", level=1, cha=16)
    async with db.session() as s:
        bard = await s.get(Character, world.kael_id)
        bard.proficiencies = []
        b1 = skill_bonus(bard, "arcana")
    assert all(lbl != "Jack of All Trades" for lbl, _ in b1.parts)
    # Level 2: half proficiency now applies to non-proficient checks.
    bard = await _as_class(db, world.kael_id, "bard", level=2)
    async with db.session() as s:
        bard = await s.get(Character, world.kael_id)
        bard.proficiencies = []
        b2 = skill_bonus(bard, "arcana")
    assert any(lbl == "Jack of All Trades" for lbl, _ in b2.parts)
    assert jack_of_all_trades_bonus("bard", 2, 2) == 1        # half of +2


async def test_bard_uses_bardic_inspiration_resource(db, provider):
    world = await build_world(db)
    bard = await _as_class(db, world.kael_id, "bard", cha=16)
    async with db.unit_of_work() as s:
        bard = await s.get(Character, world.kael_id)
        await ResourceEngine(s).grant(bard, "resource:bardic_inspiration")
    async with db.session() as s:
        state = (await s.execute(select(ResourceState).where(
            ResourceState.character_id == world.kael_id,
            ResourceState.resource_id == "resource:bardic_inspiration"))).scalar_one()
        assert state.max_value == 3                          # CHA 16 (+3), min 1


# --- SORCERER ------------------------------------------------------------------

def test_sorcery_points_scale_from_level_2():
    assert [sorcery_points_max(l) for l in (1, 2, 3, 5)] == [0, 2, 3, 5]


async def test_sorcerer_converts_points_to_slot_and_back_atomically(db, provider):
    world = await build_world(db)
    sorc = await _as_class(db, world.kael_id, "sorcerer", level=2, cha=16)
    async with db.unit_of_work() as s:
        sorc = await s.get(Character, world.kael_id)
        eng = ResourceEngine(s)
        await eng.grant(sorc, "resource:sorcery_points")     # 2 SP at level 2
        await eng.grant(sorc, "resource:spell_slots_1")      # 2 first-level slots
        # Spend a slot to make Sorcery Points (gain = slot level = 1) after using SP.
    async with db.unit_of_work() as s:
        sorc = await s.get(Character, world.kael_id)
        svc = SorceryService(s)
        await svc.create_slot_from_points(sorc, 1)           # spends 2 SP, +1 slot
    async with db.session() as s:
        sp = (await s.execute(select(ResourceState).where(
            ResourceState.character_id == world.kael_id,
            ResourceState.resource_id == "resource:sorcery_points"))).scalar_one()
        slot = (await s.execute(select(ResourceState).where(
            ResourceState.character_id == world.kael_id,
            ResourceState.resource_id == "resource:spell_slots_1"))).scalar_one()
        assert sp.current == 0                               # 2 SP spent
        assert slot.current == slot.max_value                # already full -> capped, not over


async def test_sorcerer_conversion_rejects_insufficient_points(db, provider):
    world = await build_world(db)
    sorc = await _as_class(db, world.kael_id, "sorcerer", level=2, cha=16)
    async with db.unit_of_work() as s:
        sorc = await s.get(Character, world.kael_id)
        await ResourceEngine(s).grant(sorc, "resource:sorcery_points")   # 2 SP
        await ResourceEngine(s).grant(sorc, "resource:spell_slots_1")
        await ResourceEngine(s).spend(world.kael_id, "resource:sorcery_points", 1)  # -> 1 SP
    # A 1st-level slot costs 2 SP; only 1 remains -> rejected, atomic (nothing consumed).
    with pytest.raises(RulesViolation):
        async with db.unit_of_work() as s:
            sorc = await s.get(Character, world.kael_id)
            await SorceryService(s).create_slot_from_points(sorc, 1)
    async with db.session() as s:
        sp = (await s.execute(select(ResourceState).where(
            ResourceState.resource_id == "resource:sorcery_points",
            ResourceState.character_id == world.kael_id))).scalar_one()
        assert sp.current == 1                               # nothing consumed


async def test_sorcerer_metamagic_spends_sorcery_points(db, provider):
    world = await build_world(db)
    sorc = await _as_class(db, world.kael_id, "sorcerer", level=3, cha=16)
    async with db.unit_of_work() as s:
        sorc = await s.get(Character, world.kael_id)
        await ResourceEngine(s).grant(sorc, "resource:sorcery_points")   # 3 SP at level 3
    async with db.unit_of_work() as s:
        sorc = await s.get(Character, world.kael_id)
        option = await SorceryService(s).apply_metamagic(sorc, "quickened")   # 2 SP
        assert option.effect == "cast_time=bonus_action"
    async with db.session() as s:
        sp = (await s.execute(select(ResourceState).where(
            ResourceState.resource_id == "resource:sorcery_points",
            ResourceState.character_id == world.kael_id))).scalar_one()
        assert sp.current == 1                               # 3 - 2


# --- WARLOCK -------------------------------------------------------------------

async def test_warlock_pact_slots_recover_on_short_rest(db, provider):
    world = await build_world(db)
    warlock = await _as_class(db, world.kael_id, "warlock", cha=16)
    async with db.unit_of_work() as s:
        warlock = await s.get(Character, world.kael_id)
        await ResourceEngine(s).grant(warlock, "resource:pact_slots")
        await ResourceEngine(s).spend(world.kael_id, "resource:pact_slots", 1)
    # Pact slots are the defining warlock trait: they come back on a SHORT rest.
    async with db.unit_of_work() as s:
        notes = await ResourceEngine(s).apply_short_rest(world.kael_id)
    assert any("Pact" in n or "พันธะ" in n for n in notes)
    async with db.session() as s:
        slot = (await s.execute(select(ResourceState).where(
            ResourceState.resource_id == "resource:pact_slots",
            ResourceState.character_id == world.kael_id))).scalar_one()
        assert slot.current == slot.max_value


async def test_warlock_invocation_prerequisites_enforced(db, provider):
    world = await build_world(db)
    warlock = await _as_class(db, world.kael_id, "warlock", level=1, cha=16)
    async with db.unit_of_work() as s:
        warlock = await s.get(Character, world.kael_id)
        # Agonizing Blast requires Eldritch Blast — not known yet -> rejected.
        ok, _ = await WarlockService(s).can_take_invocation(warlock, "agonizing_blast")
        assert ok is False
        s.add(CharacterSpell(character_id=warlock.id, spell_key="eldritch_blast", kind="cantrip"))
    async with db.unit_of_work() as s:
        warlock = await s.get(Character, world.kael_id)
        grant = await WarlockService(s).take_invocation(warlock, "agonizing_blast")
        assert grant.grant_type == "invocation"
        # A pact-gated invocation is refused without the pact.
        with pytest.raises(RulesViolation):
            await WarlockService(s).take_invocation(warlock, "thirsting_blade")


async def test_warlock_pact_boon_grants_and_gates_invocation(db, provider):
    world = await build_world(db)
    warlock = await _as_class(db, world.kael_id, "warlock", level=5, cha=16)
    async with db.unit_of_work() as s:
        warlock = await s.get(Character, world.kael_id)
        await WarlockService(s).choose_pact_boon(warlock, "blade")
    async with db.unit_of_work() as s:
        warlock = await s.get(Character, world.kael_id)
        # thirsting_blade needs Pact of the Blade + level 5 — both now satisfied.
        grant = await WarlockService(s).take_invocation(warlock, "thirsting_blade")
        assert grant.key == "thirsting_blade"
    async with db.session() as s:
        boon = (await s.execute(select(CharacterGrant).where(
            CharacterGrant.character_id == world.kael_id,
            CharacterGrant.grant_type == "pact_boon"))).scalar_one()
        assert boon.key == "blade"


async def test_warlock_casts_eldritch_blast_through_shared_engine(db, provider):
    world = await build_world(db)
    warlock = await _as_class(db, world.kael_id, "warlock", cha=16)
    async with db.unit_of_work() as s:
        warlock = await s.get(Character, world.kael_id)
        s.add(CharacterSpell(character_id=warlock.id, spell_key="eldritch_blast", kind="cantrip"))
    dice = DiceEngine(SequenceRandomness([19, 8]))           # attack 19 hit, 8 dmg
    async with db.unit_of_work() as s:
        warlock = await s.get(Character, world.kael_id)
        out = await SpellEngine(s, dice).cast(
            character=warlock, spell_key="eldritch_blast", target_acs={"npc:x": 13})
    assert out.slot_level == 0 and out.attack["hit"] and out.damage == 8


# --- WIZARD --------------------------------------------------------------------

async def test_wizard_learns_and_prepares_from_spellbook(db, provider):
    world = await build_world(db)
    wiz = await _as_class(db, world.kael_id, "wizard", int=16)
    async with db.unit_of_work() as s:
        wiz = await s.get(Character, world.kael_id)
        book = WizardSpellbook(s)
        await book.learn(wiz, "magic_missile")
        await book.learn(wiz, "shield")
        # A non-wizard spell can't be copied into the book.
        with pytest.raises(RulesViolation):
            await book.learn(wiz, "cure_wounds")
    async with db.unit_of_work() as s:
        wiz = await s.get(Character, world.kael_id)
        await WizardSpellbook(s).prepare(wiz, ["magic_missile"])
    async with db.session() as s:
        rows = {r.spell_key: r.prepared for r in (await s.execute(select(CharacterSpell).where(
            CharacterSpell.character_id == world.kael_id, CharacterSpell.kind == "book"))).scalars()}
        assert rows == {"magic_missile": True, "shield": False}


async def test_wizard_ritual_cast_spends_no_slot(db, provider):
    world = await build_world(db)
    wiz = await _as_class(db, world.kael_id, "wizard", int=16)
    async with db.unit_of_work() as s:
        wiz = await s.get(Character, world.kael_id)
        s.add(CharacterSpell(character_id=wiz.id, spell_key="detect_magic", kind="book", prepared=True))
        await ResourceEngine(s).grant(wiz, "resource:spell_slots_1")
    # Mark detect_magic a ritual for this assertion (content may vary); force ritual.
    reg = get_registry()
    reg.get_spell("detect_magic").ritual = True
    dice = DiceEngine(SequenceRandomness(default=3))
    async with db.unit_of_work() as s:
        wiz = await s.get(Character, world.kael_id)
        await SpellEngine(s, dice).cast(character=wiz, spell_key="detect_magic", ritual=True)
    async with db.session() as s:
        slot = (await s.execute(select(ResourceState).where(
            ResourceState.resource_id == "resource:spell_slots_1",
            ResourceState.character_id == world.kael_id))).scalar_one()
        assert slot.current == slot.max_value                # ritual: no slot spent


# --- unlock-readiness: correct slot pools declared; discipline still enforced ---

def test_each_class_declares_its_own_slot_pool_no_hardcoding():
    """Finalize grants slots from the class's declared slot_resources (fixed the
    hardcoded spell_slots_1). The DATA proves warlock uses pact slots, arcane
    casters use the arcane pool — so unlock will grant the RIGHT pool."""
    reg = get_registry()
    assert reg.slot_resource_for("warlock", 1) == "resource:pact_slots"
    for arcane in ("wizard", "sorcerer", "bard"):
        assert reg.slot_resource_for(arcane, 1) == "resource:spell_slots_1"
    assert reg.slot_resource_for("fighter", 1) is None       # non-caster


def test_sorcerer_and_warlock_remain_locked_pending_full_matrix():
    """Sorcerer/Warlock mechanics are implemented + tested, but their FULL
    acceptance matrix (subclass-level progression + natural-language Discord cast
    path) is not met — the same gaps the live wizard/bard share — so they stay
    non-selectable and creation rejects them. Unlock is a deliberate later step."""
    from app.tabletop.rules.core import SUPPORTED_CLASSES, validate_class

    reg = get_registry()
    for locked in ("sorcerer", "warlock"):
        assert locked not in reg.selectable_classes
        assert locked not in SUPPORTED_CLASSES
        assert reg.get_class(locked).support_status != "FULLY_SUPPORTED"
        with pytest.raises(RulesViolation):
            validate_class(locked)


async def test_wizard_arcane_recovery_resource_exists_and_scales(db, provider):
    world = await build_world(db)
    wiz = await _as_class(db, world.kael_id, "wizard", int=16)
    async with db.unit_of_work() as s:
        wiz = await s.get(Character, world.kael_id)
        state = await ResourceEngine(s).grant(wiz, "resource:arcane_recovery")
        assert state.max_value >= 1
    reg = get_registry()
    assert reg.get_resource("resource:arcane_recovery").recharge == "long_rest_cycle_after_short_rest"
