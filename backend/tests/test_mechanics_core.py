"""SRD 5.2.1 mechanics core: derivation with explanations, resources, rests,
typed damage, temp HP, death saves, and concentration."""
from __future__ import annotations

import pytest

from app.core.errors import RulesViolation
from app.core.randomness import SequenceRandomness
from app.models.character import Character
from app.models.progression import CharacterGrant
from app.rules_content import get_registry
from app.services.campaigns import CampaignService, CharacterService
from app.tabletop.damage import DamageComponent, DamageService
from app.tabletop.dice import DiceEngine
from app.tabletop.effects import ConcentrationService
from app.tabletop.resources import ResourceEngine
from app.tabletop.rest import RestService
from app.tabletop.rules.derive import (
    armor_class,
    max_hp_level_1,
    passive_perception,
    save_bonus,
    skill_bonus,
    spellcasting_block,
)
from tests.support.factories import build_world


def _veskan() -> Character:
    """A wizard like the playtest's Veskan: INT 16, WIS 12, proficient Arcana."""
    return Character(
        campaign_id="c", owner_member_id="m", name="Veskan",
        species="human", char_class="wizard", level=1,
        str_score=8, dex_score=12, con_score=13,
        int_score=16, wis_score=12, cha_score=10,
        proficiencies=["arcana", "investigation"], save_proficiencies=["int", "wis"],
        expertise=[], hit_die=6,
    )


# --- derivation with explanations ------------------------------------------------

def test_skill_bonus_explains_itself():
    b = skill_bonus(_veskan(), "arcana")
    assert b.total == 5                       # INT +3, Proficiency +2
    assert ("INT", 3) in b.parts and ("Proficiency", 2) in b.parts
    # Unproficient skill: ability only.
    assert skill_bonus(_veskan(), "athletics").total == -1  # STR 8 -> -1, no prof


def test_expertise_doubles_proficiency():
    v = _veskan()
    v.expertise = ["arcana"]
    b = skill_bonus(v, "arcana")
    assert b.total == 7 and ("Expertise", 4) in b.parts


def test_save_passive_and_spell_block():
    v = _veskan()
    assert save_bonus(v, "int").total == 5    # +3 INT, +2 prof
    assert save_bonus(v, "str").total == -1
    assert passive_perception(v) == 11        # 10 + WIS(+1), no proficiency
    sc = spellcasting_block(v)
    assert sc["save_dc"] == 13 and sc["attack_bonus"] == 5 and sc["ability"] == "int"


def test_hp_and_ac_from_registry():
    assert max_hp_level_1("wizard", 13, "human") == 7      # d6 + CON(+1)
    assert max_hp_level_1("wizard", 13, "dwarf") == 8      # + Dwarven Toughness
    assert armor_class("wizard", 12) == 11                 # 10 + DEX
    assert armor_class("fighter", 12) == 18                # chain mail 16 + shield


# --- resources & rests -------------------------------------------------------------

async def test_resource_spend_restore_and_bounds(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        char = await s.get(Character, world.kael_id)
        engine = ResourceEngine(s)
        state = await engine.grant(char, "resource:spell_slots_1")
        assert state.max_value == 2 and state.current == 2
        await engine.spend(char.id, "resource:spell_slots_1")
        await engine.spend(char.id, "resource:spell_slots_1")
        with pytest.raises(RulesViolation):
            await engine.spend(char.id, "resource:spell_slots_1")   # empty pool
        await engine.restore(char.id, "resource:spell_slots_1", 99)
        assert (await engine.get(char.id, "resource:spell_slots_1")).current == 2  # capped


async def test_short_rest_hit_dice_and_partial_recharge(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        bront = await s.get(Character, world.bront_id)   # fighter, CON 15 (+2)
        bront.hp = 3
        bront.hit_dice_remaining = 1
        bront.hit_die = 10
        engine = ResourceEngine(s)
        sw = await engine.grant(bront, "resource:second_wind")
        sw.current = 0                                   # both uses spent

    rest = RestService(db, SequenceRandomness([6]))       # hit die rolls 6
    outcome = await rest.short_rest(
        campaign_id=world.campaign_id, character_ids=[world.bront_id],
        spend_hit_dice={world.bront_id: 1},
    )
    assert outcome.completed
    async with db.session() as s:
        bront = await s.get(Character, world.bront_id)
        assert bront.hp == 3 + 6 + 2                      # roll + CON mod
        assert bront.hit_dice_remaining == 0
        sw = await ResourceEngine(s).get(world.bront_id, "resource:second_wind")
        assert sw.current == 1                            # short-rest partial +1


async def test_long_rest_full_recovery_and_exhaustion(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        kael = await s.get(Character, world.kael_id)
        kael.hp = 1
        kael.exhaustion = 2
        kael.hit_dice_remaining = 0
        await ResourceEngine(s).grant(kael, "resource:spell_slots_1")
        await ResourceEngine(s).spend(kael.id, "resource:spell_slots_1", 2)

    outcome = await RestService(db, SequenceRandomness(default=4)).long_rest(
        campaign_id=world.campaign_id, character_ids=[world.kael_id],
    )
    assert outcome.completed
    async with db.session() as s:
        kael = await s.get(Character, world.kael_id)
        assert kael.hp == kael.max_hp
        assert kael.exhaustion == 1                       # −1 per long rest
        assert kael.hit_dice_remaining == 1               # regained half (min 1)
        slots = await ResourceEngine(s).get(world.kael_id, "resource:spell_slots_1")
        assert slots.current == 2                         # slots restored


async def test_rest_interrupted_by_perceivable_event_gives_no_benefits(db):
    from app.world import ThreatService

    world = await build_world(db)
    async with db.unit_of_work() as s:
        kael = await s.get(Character, world.kael_id)
        kael.hp = 2
        await ThreatService(s).schedule_event(
            campaign_id=world.campaign_id, due_game_time=30, kind="ambush",
            payload={"summary": "เสียงฝีเท้าหลายคู่เข้ามาใกล้"}, perceivable=True,
        )
    outcome = await RestService(db, SequenceRandomness(default=4)).short_rest(
        campaign_id=world.campaign_id, character_ids=[world.kael_id],
        spend_hit_dice={world.kael_id: 1},
    )
    assert outcome.completed is False
    assert outcome.interrupted_by == ["เสียงฝีเท้าหลายคู่เข้ามาใกล้"]
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).hp == 2   # no benefits


# --- damage pipeline ------------------------------------------------------------------

async def test_typed_damage_with_resistance_per_component(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        bront = await s.get(Character, world.bront_id)   # dwarf
        bront.hp = 20
        bront.max_hp = 20
        grants = [CharacterGrant(character_id=bront.id, grant_type="trait",
                                 key="dwarven_resilience", source_type="SPECIES",
                                 source_key="species:dwarf",
                                 data={"resistances": ["poison"]})]
        result = await DamageService(s).apply_damage(
            target=bront,
            components=[DamageComponent(7, "slashing"), DamageComponent(5, "poison")],
            character_grants=grants,
        )
    # Components resolved independently: 7 slashing + floor(5/2)=2 poison = 9.
    assert result.total == 9
    assert result.hp_after == 11
    poison = next(c for c in result.components if c.damage_type == "poison")
    assert poison.final == 2 and poison.note == "ต้านทาน"


async def test_temp_hp_absorbs_first_and_never_stacks(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        kael = await s.get(Character, world.kael_id)
        kael.hp = 9
        DamageService.grant_temp_hp(kael, 5)
        DamageService.grant_temp_hp(kael, 3)              # keep-higher: still 5
        assert kael.temp_hp == 5
        result = await DamageService(s).apply_damage(
            target=kael, components=[DamageComponent(7, "piercing")],
        )
    assert result.absorbed_by_temp_hp == 5
    assert result.hp_after == 7                           # 9 - (7-5)


async def test_zero_hp_dying_death_saves_and_instant_death(db):
    world = await build_world(db)
    dice = DiceEngine(SequenceRandomness([15, 4, 1]))     # success, fail, nat1(=2 fails)
    async with db.unit_of_work() as s:
        kael = await s.get(Character, world.kael_id)
        kael.hp = 5
        kael.max_hp = 9
        result = await DamageService(s).apply_damage(
            target=kael, components=[DamageComponent(6, "slashing")],
        )
        assert result.dying and not result.dead and kael.hp == 0

        # Death saves: d20, DC 10; nat 1 = two failures; 3 fails = dead.
        for face, expect in ((15, {"successes": 1, "failures": 0}),
                             (4, {"successes": 1, "failures": 1}),
                             (1, {"successes": 1, "failures": 3})):
            roll, _ = dice.roll_d20()
            saves = dict(kael.death_saves)
            if roll == 1:
                saves["failures"] += 2
            elif roll >= 10:
                saves["successes"] += 1
            else:
                saves["failures"] += 1
            kael.death_saves = saves
            assert saves == expect
        # (The full death-save loop lives in combat; here we exercise the SRD math.)

    # Instant death: excess damage >= max HP.
    async with db.unit_of_work() as s:
        bront = await s.get(Character, world.bront_id)
        bront.hp = 3
        bront.max_hp = 13
        result = await DamageService(s).apply_damage(
            target=bront, components=[DamageComponent(16, "bludgeoning")],
        )   # 16 - 3 = 13 excess >= 13 max
        assert result.dead


async def test_healing_revives_the_dying(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        kael = await s.get(Character, world.kael_id)
        kael.hp = 0
        kael.death_saves = {"successes": 1, "failures": 2}
        result = await DamageService(s).heal(target=kael, amount=4)
        assert result.revived and kael.hp == 4
        assert kael.death_saves == {"successes": 0, "failures": 0}


# --- concentration ---------------------------------------------------------------------

async def test_concentration_one_at_a_time_and_damage_save(db):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        kael = await s.get(Character, world.kael_id)
        kael.hp = 9
        conc = ConcentrationService(s, DiceEngine(SequenceRandomness([3])))
        first = await conc.begin(character=kael, name="Detect Magic",
                                 spell_key="detect_magic")
        # Beginning a second effect ends the first.
        second = await conc.begin(character=kael, name="Fog Cloud",
                                  spell_key="fog_cloud")
        assert (await conc.current(kael.id)).id == second.id
        await s.refresh(first)
        assert first.active is False

        # Damage forces a CON save: 22 damage -> DC 11; roll 3 + CON(+0)=3 fails.
        kael.max_hp = 30
        kael.hp = 30
        result = await DamageService(s, DiceEngine(SequenceRandomness([3]))).apply_damage(
            target=kael, components=[DamageComponent(22, "fire")],
        )
        assert result.concentration_save == {
            "dc": 11, "passed": False, "effect": "Fog Cloud",
            "natural_roll": 3, "total": 3,
        }
        assert await conc.current(kael.id) is None        # effect dropped
