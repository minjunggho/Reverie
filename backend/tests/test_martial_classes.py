"""Martial classes — Fighter, Rogue, Barbarian, Monk on the shared systems.

Signature abilities are usable through natural gameplay (the feature-activation
pipeline), resources spend/recover, Unarmored Defense computes, Sneak Attack fires
ONLY on its real conditions, Rage modifies combat, and creation → feature use →
combat → rest → restart → subclass all work. Barbarian/Monk were unlocked; their
end-to-end path is the gate below.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import AdminBridge, InboundMessage, is_admin_command
from app.engine import build_bridge
from app.models.character import Character
from app.models.character_draft import CharacterDraft
from app.models.progression import ActiveEffect, CharacterGrant, ResourceState
from app.presentation import MessageKind
from app.rules_content import get_registry
from app.tabletop.classes.features import ClassFeatureService, active_rage
from app.tabletop.classes.martial_combat import (
    extra_attacks,
    rage_damage_after_resistance,
    sneak_attack_dice,
    sneak_attack_eligible,
)
from app.tabletop.dice import DiceEngine
from app.tabletop.resources import ResourceEngine
from app.tabletop.rules.derive import armor_class
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1", name="กี้"):
    _n["v"] += 1
    return InboundMessage(discord_message_id=f"mc{_n['v']}", guild_id="guild-1",
                          channel_id="chan-1", author_discord_id=author,
                          author_display_name=name, content=content)


class Table:
    def __init__(self, db, provider, rng=None):
        self.game = build_bridge(db, provider=provider, rng=rng or SequenceRandomness(default=6))
        self.admin = AdminBridge(db, provider, creation_flow=self.game.creation_flow,
                                 session_zero=self.game.session_zero)

    async def send(self, content, author="disc-p1", name="กี้"):
        inbound = _msg(content, author=author, name=name)
        if is_admin_command(content):
            return await self.admin.handle(inbound)
        return await self.game.handle_inbound(inbound)


async def _as(db, cid, char_class, *, level=1, grant_features=True, **scores):
    """Make a character a given class/level and (optionally) grant its features +
    their resources — the state finalize/level_up would produce."""
    async with db.unit_of_work() as s:
        c = await s.get(Character, cid)
        c.char_class = char_class
        c.level = level
        for k, v in scores.items():
            setattr(c, f"{k}_score", v)
        if grant_features:
            reg = get_registry()
            eng = ResourceEngine(s)
            for f in reg.get_class(char_class).features_at(level):
                s.add(CharacterGrant(character_id=c.id, grant_type="feature", key=f.key,
                                     name_th=f.name_th, source_type="CLASS",
                                     source_key=f"class:{char_class}"))
                if f.resource_id and await eng.get(c.id, f.resource_id) is None:
                    await eng.grant(c, f.resource_id)
    async with db.session() as s:
        return await s.get(Character, cid)


# --- unlock gate ----------------------------------------------------------------

def test_all_four_martial_classes_are_selectable():
    from app.tabletop.rules.core import SUPPORTED_CLASSES, validate_class

    reg = get_registry()
    for c in ("fighter", "rogue", "barbarian", "monk"):
        assert c in reg.selectable_classes and c in SUPPORTED_CLASSES
        assert reg.get_class(c).support_status == "FULLY_SUPPORTED"
        assert validate_class(c) == c


def test_features_land_at_correct_levels():
    reg = get_registry()
    assert "extra_attack" not in {f.key for f in reg.get_class("fighter").features_at(4)}
    assert "extra_attack" in {f.key for f in reg.get_class("fighter").features_at(5)}
    assert "indomitable" in {f.key for f in reg.get_class("fighter").features_at(9)}
    assert "cunning_action" in {f.key for f in reg.get_class("rogue").features_at(2)}
    assert "evasion" in {f.key for f in reg.get_class("rogue").features_at(7)}
    assert "reckless_attack" in {f.key for f in reg.get_class("barbarian").features_at(2)}
    assert "stunning_strike" in {f.key for f in reg.get_class("monk").features_at(5)}
    assert "flurry_of_blows" not in {f.key for f in reg.get_class("monk").features_at(1)}


# --- unarmored defense ----------------------------------------------------------

def test_unarmored_defense_barbarian_con_and_monk_wis():
    # Barbarian: 10 + DEX + CON; Monk: 10 + DEX + WIS.
    assert armor_class("barbarian", 14, con_score=16) == 15   # 10+2+3
    assert armor_class("monk", 16, wis_score=14) == 15         # 10+3+2


# --- fighter: Second Wind + Action Surge through the pipeline --------------------

async def test_fighter_second_wind_heals_through_the_activation_pipeline(db, provider):
    world = await build_world(db)
    await _as(db, world.kael_id, "fighter", level=3, con=14)
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        c.max_hp = 30
        c.hp = 5
    sid, _ = await start_session_with_scene(db, world)
    table = Table(db, provider, rng=SequenceRandomness([7]))    # d10=7 + level 3 = 10 heal
    r = await table.send("! ใช้ Second Wind")
    assert r.responses[0].kind == MessageKind.CHECK_RESOLUTION and r.state_mutated
    async with db.session() as s:
        c = await s.get(Character, world.kael_id)
        assert c.hp == 15                                       # 5 + (7+3)
        slot = (await s.execute(select(ResourceState).where(
            ResourceState.character_id == c.id,
            ResourceState.resource_id == "resource:second_wind"))).scalar_one()
        assert slot.current == slot.max_value - 1              # one use spent


async def test_second_wind_rejected_when_exhausted_consumes_nothing(db, provider):
    world = await build_world(db)
    await _as(db, world.kael_id, "fighter", level=1, con=14)
    async with db.unit_of_work() as s:
        await ResourceEngine(s).spend(world.kael_id, "resource:second_wind", 2)  # drain (max 2)
    sid, _ = await start_session_with_scene(db, world)
    table = Table(db, provider)
    r = await table.send("! ใช้ Second Wind")
    assert r.responses[0].kind == MessageKind.TABLE_NOTICE      # diagnostic, not a heal
    async with db.session() as s:
        slot = (await s.execute(select(ResourceState).where(
            ResourceState.resource_id == "resource:second_wind",
            ResourceState.character_id == world.kael_id))).scalar_one()
        assert slot.current == 0


# --- rogue: Sneak Attack eligibility (validated, never vibes) --------------------

def test_sneak_attack_only_on_actual_conditions():
    assert sneak_attack_dice(1) == 1 and sneak_attack_dice(5) == 3
    # advantage + finesse, no disadvantage -> eligible
    assert sneak_attack_eligible(weapon_finesse_or_ranged=True, has_advantage=True,
                                 ally_adjacent_to_target=False, has_disadvantage=False)
    # an ally adjacent (no advantage) -> eligible
    assert sneak_attack_eligible(weapon_finesse_or_ranged=True, has_advantage=False,
                                 ally_adjacent_to_target=True, has_disadvantage=False)
    # disadvantage -> NEVER, even with an ally adjacent
    assert not sneak_attack_eligible(weapon_finesse_or_ranged=True, has_advantage=True,
                                     ally_adjacent_to_target=True, has_disadvantage=True)
    # a non-finesse/ranged weapon -> never
    assert not sneak_attack_eligible(weapon_finesse_or_ranged=False, has_advantage=True,
                                     ally_adjacent_to_target=True, has_disadvantage=False)
    # neither advantage nor an ally -> never (sounding sneaky is not enough)
    assert not sneak_attack_eligible(weapon_finesse_or_ranged=True, has_advantage=False,
                                     ally_adjacent_to_target=False, has_disadvantage=False)


# --- barbarian: Rage end-to-end -------------------------------------------------

async def test_barbarian_rage_activates_and_modifies_combat(db, provider):
    from app.models.combat import Combatant
    from app.tabletop.combat import CombatantSpec, CombatService

    world = await build_world(db)
    await _as(db, world.kael_id, "barbarian", level=5, str=16, con=14)
    sid, _ = await start_session_with_scene(db, world)
    # Enter Rage through natural gameplay.
    table = Table(db, provider)
    r = await table.send("! เข้าโหมดเกรี้ยวกราด")
    assert r.state_mutated and "เกรี้ยวกราด" in r.responses[0].content
    async with db.session() as s:
        assert await active_rage(s, world.kael_id) is not None
        rage = (await active_rage(s, world.kael_id)).data
        assert rage["damage_bonus"] == 2 and "slashing" in rage["resistances"]
        slot = (await s.execute(select(ResourceState).where(
            ResourceState.resource_id == "resource:rage",
            ResourceState.character_id == world.kael_id))).scalar_one()
        assert slot.current == slot.max_value - 1              # a rage use spent

    # In combat, the raging attacker's melee damage gets the +2 bonus.
    async with db.unit_of_work() as s:
        enc = await CombatService(s, DiceEngine(SequenceRandomness(default=10))).start_combat(
            campaign_id=world.campaign_id, session_id=sid,
            specs=[CombatantSpec(entity_ref=f"character:{world.kael_id}", name="Kael",
                                 hp=45, max_hp=45, ac=15, is_pc=True, attack_bonus=7,
                                 damage_die=12, damage_bonus=3),
                   CombatantSpec(entity_ref="npc:goblin", name="Goblin", hp=30, max_hp=30, ac=10)])
        enc_id = enc.id
    async with db.unit_of_work() as s:
        svc = CombatService(s, DiceEngine(SequenceRandomness([15, 6])))  # hit, dmg die=6
        enc = await svc.get_encounter(enc_id) if hasattr(svc, "get_encounter") else None
        from app.models.combat import CombatEncounter
        enc = await s.get(CombatEncounter, enc_id)
        kael_cb = (await s.execute(select(Combatant).where(
            Combatant.entity_ref == f"character:{world.kael_id}"))).scalar_one()
        goblin = (await s.execute(select(Combatant).where(
            Combatant.entity_ref == "npc:goblin"))).scalar_one()
        # ensure it's Kael's turn
        enc.turn_index = enc.initiative_order.index(kael_cb.id)
        kael_cb.has_action = True
        out = await svc.attack(enc, attacker_id=kael_cb.id, target_id=goblin.id)
    # damage = die(6) + bonus(3) + rage(2) = 11
    assert out.damage == 11


async def test_raging_target_resists_physical_damage_helper():
    assert rage_damage_after_resistance(10, "slashing", target_raging=True) == 5
    assert rage_damage_after_resistance(10, "fire", target_raging=True) == 10   # not physical
    assert rage_damage_after_resistance(10, "slashing", target_raging=False) == 10


# --- monk: Ki abilities spend Focus ---------------------------------------------

async def test_monk_flurry_of_blows_spends_focus(db, provider):
    world = await build_world(db)
    await _as(db, world.kael_id, "monk", level=2, dex=16, wis=14)
    sid, _ = await start_session_with_scene(db, world)
    table = Table(db, provider)
    r = await table.send("! หมัดรัว")
    assert r.state_mutated and "หมัดรัว" in r.responses[0].content
    async with db.session() as s:
        focus = (await s.execute(select(ResourceState).where(
            ResourceState.resource_id == "resource:focus",
            ResourceState.character_id == world.kael_id))).scalar_one()
        assert focus.current == focus.max_value - 1           # 1 Focus spent


async def test_focus_recovers_on_short_rest(db, provider):
    world = await build_world(db)
    await _as(db, world.kael_id, "monk", level=2, dex=16, wis=14)
    async with db.unit_of_work() as s:
        await ResourceEngine(s).spend(world.kael_id, "resource:focus", 2)
    async with db.unit_of_work() as s:
        await ResourceEngine(s).apply_short_rest(world.kael_id)
    async with db.session() as s:
        focus = (await s.execute(select(ResourceState).where(
            ResourceState.resource_id == "resource:focus",
            ResourceState.character_id == world.kael_id))).scalar_one()
        assert focus.current == focus.max_value               # ki back on a SHORT rest


# --- creation → finalize → restart persistence ----------------------------------

async def _finalize(db, world, char_class, background):
    from app.services.campaigns.finalize import finalize_character

    reg = get_registry()
    cls = reg.get_class(char_class)
    skills = (list(cls.skill_choices["options"])[:cls.skill_choices["count"]]
              if cls.skill_choices["options"] != "any" else ["athletics", "intimidation"])
    build = {"step": "review", "class": char_class, "species": "human", "background": background,
             "scores": {"str": 16, "dex": 14, "con": 15, "int": 8, "wis": 13, "cha": 10},
             "skills": skills, "component_token": "t"}
    async with db.unit_of_work() as s:
        d = CharacterDraft(campaign_id=world.campaign_id, member_id=world.p1_member_id,
                           data={"name": f"E2E{char_class.title()}", "_build": build})
        s.add(d); await s.flush(); did = d.id
    async with db.session() as s:
        d = await s.get(CharacterDraft, did)
    r = await finalize_character(db, draft=d, data=d.data, channel_id="chan-1")
    assert r.responses[0].kind == MessageKind.CHARACTER_REVEAL
    async with db.session() as s:
        return (await s.execute(select(Character).where(
            Character.name == f"E2E{char_class.title()}"))).scalar_one()


async def test_barbarian_creates_with_unarmored_ac_and_rage_resource(db, provider):
    world = await build_world(db)
    barb = await _finalize(db, world, "barbarian", "soldier")
    # Unarmored Defense: 10 + DEX(14→+2) + CON(15→+2) = 14.
    assert barb.ac == 14
    async with db.session() as s:
        rids = {r.resource_id for r in (await s.execute(select(ResourceState).where(
            ResourceState.character_id == barb.id))).scalars()}
        assert "resource:rage" in rids                        # signature resource granted
        feats = {g.key for g in (await s.execute(select(CharacterGrant).where(
            CharacterGrant.character_id == barb.id,
            CharacterGrant.grant_type == "feature"))).scalars()}
        assert {"rage", "unarmored_defense"} <= feats


async def test_monk_creates_with_wis_ac_and_martial_arts(db, provider):
    world = await build_world(db)
    monk = await _finalize(db, world, "monk", "criminal")
    assert monk.ac == 10 + 2 + 1                               # DEX14(+2) + WIS13(+1)
    async with db.session() as s:
        feats = {g.key for g in (await s.execute(select(CharacterGrant).where(
            CharacterGrant.character_id == monk.id,
            CharacterGrant.grant_type == "feature"))).scalars()}
        assert "martial_arts" in feats


async def test_rage_effect_and_resource_survive_restart(tmp_path, provider):
    from app.db.session import Database

    url = f"sqlite+aiosqlite:///{(tmp_path / 'rage.db').as_posix()}"
    first = Database(url, echo=False)
    await first.create_all()
    try:
        world = await build_world(first)
        await _as(first, world.kael_id, "barbarian", level=1, str=16, con=14)
        async with first.unit_of_work() as s:
            c = await s.get(Character, world.kael_id)
            await ClassFeatureService(s, DiceEngine(SequenceRandomness(default=6))).activate(c, "rage")
    finally:
        await first.dispose()

    restarted = Database(url, echo=False)
    try:
        async with restarted.session() as s:
            rage = await active_rage(s, world.kael_id)
            assert rage is not None and rage.data["damage_bonus"] == 2   # effect persisted
            slot = (await s.execute(select(ResourceState).where(
                ResourceState.resource_id == "resource:rage",
                ResourceState.character_id == world.kael_id))).scalar_one()
            assert slot.current == slot.max_value - 1                    # spend persisted
    finally:
        await restarted.dispose()


# --- level + subclass progression -----------------------------------------------

async def test_barbarian_levels_to_subclass_and_grants_features(db, provider):
    from app.tabletop.progression import level_up

    world = await build_world(db)
    await _as(db, world.kael_id, "barbarian", level=2, str=16, con=14)
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        result = await level_up(s, c, chosen_subclass="path_of_the_berserker")
    assert result["level"] == 3
    async with db.session() as s:
        c = await s.get(Character, world.kael_id)
        assert c.active_subclass == "path_of_the_berserker"
        feats = {g.key for g in (await s.execute(select(CharacterGrant).where(
            CharacterGrant.character_id == c.id,
            CharacterGrant.grant_type == "subclass_feature"))).scalars()}
        assert "frenzy" in feats                              # subclass feature granted once


async def test_extra_attack_count_by_level():
    assert extra_attacks("fighter", 4, {"second_wind"}) == 1
    assert extra_attacks("fighter", 5, {"extra_attack"}) == 2
    assert extra_attacks("barbarian", 5, {"extra_attack"}) == 2
    assert extra_attacks("rogue", 5, {"expertise"}) == 1      # rogue has no Extra Attack
