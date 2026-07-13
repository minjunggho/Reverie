"""Caster classes — Cleric, Druid, Paladin, Ranger on the shared systems.

Reuses the feature-activation pipeline + SpellEngine. The genuinely-new piece is
Wild Shape with AUTHORITATIVE beast-form data (never LLM invention). Lay on Hands
is a class feature that NEVER enters a spell pool. The old prepared-spell deadlock
stays covered. Druid + Paladin were unlocked only after this gate passed.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import RulesViolation
from app.core.randomness import SequenceRandomness
from app.discord_bridge import AdminBridge, InboundMessage, is_admin_command
from app.engine import build_bridge
from app.models.character import Character
from app.models.character_draft import CharacterDraft
from app.models.progression import ActiveEffect, CharacterGrant, CharacterSpell, ResourceState
from app.presentation import MessageKind
from app.rules_content import get_registry
from app.tabletop.classes.druid import WildShapeService
from app.tabletop.classes.features import ClassFeatureService
from app.tabletop.dice import DiceEngine
from app.tabletop.resources import ResourceEngine
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1", name="กี้"):
    _n["v"] += 1
    return InboundMessage(discord_message_id=f"dn{_n['v']}", guild_id="guild-1",
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


async def _as(db, cid, char_class, *, level=1, grant_features=True, spells=(), **scores):
    async with db.unit_of_work() as s:
        c = await s.get(Character, cid)
        c.char_class = char_class
        c.level = level
        c.wis_score = c.cha_score = 16
        for k, v in scores.items():
            setattr(c, f"{k}_score", v)
        reg = get_registry()
        eng = ResourceEngine(s)
        if grant_features:
            for f in reg.get_class(char_class).features_at(level):
                s.add(CharacterGrant(character_id=c.id, grant_type="feature", key=f.key,
                                     name_th=f.name_th, source_type="CLASS",
                                     source_key=f"class:{char_class}"))
                if f.resource_id and await eng.get(c.id, f.resource_id) is None:
                    await eng.grant(c, f.resource_id)
        for sp in spells:
            s.add(CharacterSpell(character_id=c.id, spell_key=sp, kind="known", prepared=True))
        rid = reg.slot_resource_for(char_class, 1)
        if rid and await eng.get(c.id, rid) is None:
            await eng.grant(c, rid)
    async with db.session() as s:
        return await s.get(Character, cid)


# --- unlock gate ----------------------------------------------------------------

def test_all_four_caster_classes_selectable():
    from app.tabletop.rules.core import SUPPORTED_CLASSES, validate_class

    reg = get_registry()
    for c in ("cleric", "druid", "paladin", "ranger"):
        assert c in reg.selectable_classes and c in SUPPORTED_CLASSES
        assert reg.get_class(c).support_status == "FULLY_SUPPORTED"
        assert validate_class(c) == c


def test_caster_spellcasting_at_correct_levels():
    reg = get_registry()
    # Ranger/Cleric/Druid cast from L1; Paladin (half-caster) from L2.
    assert "spellcasting" in {f.key for f in reg.get_class("cleric").features_at(1)}
    assert "spellcasting" in {f.key for f in reg.get_class("ranger").features_at(1)}
    assert "spellcasting" in {f.key for f in reg.get_class("druid").features_at(1)}
    assert "spellcasting" not in {f.key for f in reg.get_class("paladin").features_at(1)}
    assert "spellcasting" in {f.key for f in reg.get_class("paladin").features_at(2)}


# --- Paladin: Lay on Hands is a FEATURE, never a spell --------------------------

def test_lay_on_hands_is_never_in_a_spell_pool():
    """Regression: Lay on Hands must never be selectable/castable as a spell."""
    reg = get_registry()
    assert "lay_on_hands" not in reg.spells
    for lvl in (0, 1):
        assert not any(s.name == "lay_on_hands"
                       for s in reg.spells_for_class("paladin", lvl))
    # It IS a class feature backed by a resource pool.
    feat = next(f for f in reg.get_class("paladin").features if f.key == "lay_on_hands")
    assert feat.resource_id == "resource:lay_on_hands" and feat.execution == "supported"


async def test_lay_on_hands_heals_from_its_pool_through_activation(db, provider):
    world = await build_world(db)
    await _as(db, world.kael_id, "paladin", level=3, str=16, cha=14)  # pool = 5*3 = 15
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        c.max_hp = 30
        c.hp = 20
    sid, _ = await start_session_with_scene(db, world)
    table = Table(db, provider)
    r = await table.send("! ใช้วางมือรักษา")
    assert r.state_mutated and "วางมือรักษา" in r.responses[0].content
    async with db.session() as s:
        c = await s.get(Character, world.kael_id)
        assert c.hp > 20                                        # healed
        pool = (await s.execute(select(ResourceState).where(
            ResourceState.resource_id == "resource:lay_on_hands",
            ResourceState.character_id == c.id))).scalar_one()
        assert pool.current < pool.max_value                   # points drawn from the pool


async def test_divine_smite_spends_a_spell_slot(db, provider):
    world = await build_world(db)
    await _as(db, world.kael_id, "paladin", level=2, str=16, cha=14)
    sid, _ = await start_session_with_scene(db, world)
    table = Table(db, provider, rng=SequenceRandomness(default=5))
    r = await table.send("! ตวัดศักดิ์สิทธิ์")
    assert r.state_mutated and "radiant" in r.responses[0].content
    async with db.session() as s:
        slot = (await s.execute(select(ResourceState).where(
            ResourceState.resource_id == "resource:spell_slots_1",
            ResourceState.character_id == world.kael_id))).scalar_one()
        assert slot.current == slot.max_value - 1              # a slot was spent


# --- prepared-spell deadlock regression (permanent) -----------------------------

async def test_prepared_caster_finalizes_without_the_old_deadlock(db, provider):
    """The old prepared-spell creation deadlock stays fixed: a prepared caster
    finalizes with a completed, non-empty prepared set and its slots."""
    world = await build_world(db)
    cleric = await _finalize(db, world, "cleric",
                             cantrips=["sacred_flame", "guidance", "light"],
                             prepared=["cure_wounds", "bless"])
    async with db.session() as s:
        spells = {r.spell_key: r.prepared for r in (await s.execute(select(CharacterSpell).where(
            CharacterSpell.character_id == cleric.id))).scalars()}
        assert spells.get("cure_wounds") is True and spells.get("sacred_flame") is not None
        rids = {r.resource_id for r in (await s.execute(select(ResourceState).where(
            ResourceState.character_id == cleric.id))).scalars()}
        assert "resource:spell_slots_1" in rids


# --- Cleric: Channel Divinity ---------------------------------------------------

async def test_cleric_channel_divinity_spends_its_resource(db, provider):
    world = await build_world(db)
    await _as(db, world.kael_id, "cleric", level=2, wis=16)
    sid, _ = await start_session_with_scene(db, world)
    table = Table(db, provider)
    r = await table.send("! ใช้ channel divinity")
    assert r.state_mutated
    async with db.session() as s:
        cd = (await s.execute(select(ResourceState).where(
            ResourceState.resource_id == "resource:channel_divinity",
            ResourceState.character_id == world.kael_id))).scalar_one()
        assert cd.current == cd.max_value - 1


# --- Druid: Wild Shape with AUTHORITATIVE form data -----------------------------

def test_wild_shape_forms_are_authoritative_and_cr_gated():
    reg = get_registry()
    assert set(reg.beast_forms) >= {"wolf", "brown_bear", "giant_eagle"}
    # A level-2 druid may only become the low-CR wolf; the bear is too high.
    l2 = {f.key for f in reg.legal_beast_forms(2)}
    assert "wolf" in l2 and "brown_bear" not in l2
    assert "brown_bear" in {f.key for f in reg.legal_beast_forms(8)}


async def test_wild_shape_transform_uses_form_stats_and_reverts_cleanly(db, provider):
    world = await build_world(db)
    await _as(db, world.kael_id, "druid", level=2, wis=16)
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        c.max_hp = 12
        c.hp = 12
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        result = await WildShapeService(s).transform(c, "wolf")
    assert result.form_hp == 11 and result.ac == 13           # authoritative wolf stats
    async with db.session() as s:
        c = await s.get(Character, world.kael_id)
        eff = await WildShapeService(s).current_form(c.id)
        assert eff is not None and eff.data["form"] == "wolf"
        assert eff.data["attack"]["damage"] == "2d4 piercing"  # from BeastFormDef, not LLM
        assert c.temp_hp == 11                                  # form HP as a temp pool
        assert c.hp == 12                                       # own HP untouched
        ws = (await s.execute(select(ResourceState).where(
            ResourceState.resource_id == "resource:wild_shape",
            ResourceState.character_id == c.id))).scalar_one()
        assert ws.current == ws.max_value - 1                   # a use spent
    # Revert restores the druid cleanly.
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        assert await WildShapeService(s).revert(c) is True
    async with db.session() as s:
        c = await s.get(Character, world.kael_id)
        assert await WildShapeService(s).current_form(c.id) is None
        assert c.temp_hp == 0 and c.hp == 12


async def test_wild_shape_refuses_illegal_form_and_spends_nothing(db, provider):
    world = await build_world(db)
    await _as(db, world.kael_id, "druid", level=2, wis=16)
    with pytest.raises(RulesViolation):
        async with db.unit_of_work() as s:
            c = await s.get(Character, world.kael_id)
            await WildShapeService(s).transform(c, "brown_bear")   # CR too high at L2
    with pytest.raises(RulesViolation):
        async with db.unit_of_work() as s:
            c = await s.get(Character, world.kael_id)
            await WildShapeService(s).transform(c, "tarrasque")    # not a defined form
    async with db.session() as s:
        ws = (await s.execute(select(ResourceState).where(
            ResourceState.resource_id == "resource:wild_shape",
            ResourceState.character_id == world.kael_id))).scalar_one()
        assert ws.current == ws.max_value                        # nothing spent


async def test_wild_shape_through_the_activation_pipeline(db, provider):
    world = await build_world(db)
    await _as(db, world.kael_id, "druid", level=2, wis=16)
    sid, _ = await start_session_with_scene(db, world)
    table = Table(db, provider)
    r = await table.send("! แปลงร่าง")                          # defaults to the legal wolf
    assert r.state_mutated and "แปลงร่าง" in r.responses[0].content
    async with db.session() as s:
        assert await WildShapeService(s).current_form(world.kael_id) is not None


async def test_wild_shape_use_recovers_on_short_rest_and_survives_restart(tmp_path, provider):
    from app.db.session import Database

    url = f"sqlite+aiosqlite:///{(tmp_path / 'ws.db').as_posix()}"
    first = Database(url, echo=False)
    await first.create_all()
    try:
        world = await build_world(first)
        await _as(first, world.kael_id, "druid", level=2, wis=16)
        async with first.unit_of_work() as s:
            c = await s.get(Character, world.kael_id)
            await WildShapeService(s).transform(c, "wolf")
    finally:
        await first.dispose()
    restarted = Database(url, echo=False)
    try:
        async with restarted.session() as s:
            assert await WildShapeService(s).current_form(world.kael_id) is not None  # persisted
        async with restarted.unit_of_work() as s:
            await ResourceEngine(s).apply_short_rest(world.kael_id)  # Wild Shape recharges
        async with restarted.session() as s:
            ws = (await s.execute(select(ResourceState).where(
                ResourceState.resource_id == "resource:wild_shape",
                ResourceState.character_id == world.kael_id))).scalar_one()
            assert ws.current == ws.max_value
    finally:
        await restarted.dispose()


# --- creation + subclass + hunter's mark ----------------------------------------

async def _finalize(db, world, char_class, *, cantrips=(), prepared=()):
    from app.services.campaigns.finalize import finalize_character

    reg = get_registry()
    cls = reg.get_class(char_class)
    skills = (list(cls.skill_choices["options"])[:cls.skill_choices["count"]]
              if cls.skill_choices["options"] != "any" else ["nature", "perception"])
    build = {"step": "review", "class": char_class, "species": "human", "background": "acolyte",
             "scores": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 16, "cha": 13},
             "skills": skills, "cantrips": list(cantrips), "prepared": list(prepared),
             "component_token": "t"}
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


async def test_druid_creates_with_wild_shape_ungranted_at_level_1(db, provider):
    world = await build_world(db)
    druid = await _finalize(db, world, "druid",
                            cantrips=["druidcraft", "guidance"], prepared=["cure_wounds", "goodberry"])
    async with db.session() as s:
        feats = {g.key for g in (await s.execute(select(CharacterGrant).where(
            CharacterGrant.character_id == druid.id,
            CharacterGrant.grant_type == "feature"))).scalars()}
        assert "druidic" in feats and "wild_shape" not in feats  # Wild Shape is L2


async def test_paladin_level1_has_lay_on_hands_but_no_spell_slots(db, provider):
    world = await build_world(db)
    pal = await _finalize(db, world, "paladin")
    async with db.session() as s:
        rids = {r.resource_id for r in (await s.execute(select(ResourceState).where(
            ResourceState.character_id == pal.id))).scalars()}
        assert "resource:lay_on_hands" in rids                  # L1 feature
        assert "resource:spell_slots_1" not in rids             # half-caster: spells at L2
        feats = {g.key for g in (await s.execute(select(CharacterGrant).where(
            CharacterGrant.character_id == pal.id,
            CharacterGrant.grant_type == "feature"))).scalars()}
        assert "lay_on_hands" in feats and "divine_sense" in feats


async def test_druid_levels_to_circle_subclass_and_grants_feature(db, provider):
    from app.tabletop.progression import level_up

    world = await build_world(db)
    await _as(db, world.kael_id, "druid", level=2, wis=16)
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        await level_up(s, c, chosen_subclass="circle_of_the_moon")
    async with db.session() as s:
        c = await s.get(Character, world.kael_id)
        assert c.active_subclass == "circle_of_the_moon"
        feats = {g.key for g in (await s.execute(select(CharacterGrant).where(
            CharacterGrant.character_id == c.id,
            CharacterGrant.grant_type == "subclass_feature"))).scalars()}
        assert "combat_wild_shape" in feats


async def test_ranger_hunters_mark_casts_through_the_spell_engine(db, provider):
    world = await build_world(db)
    await _as(db, world.kael_id, "ranger", level=1, wis=16, spells=["hunters_mark"])
    sid, _ = await start_session_with_scene(db, world)
    from app.schemas.llm_io import ActionInterpretation
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ทำเครื่องหมายล่า", method="ร่าย", intent_confidence=0.9, cast_intent=True,
        spell_reference="hunters_mark", target_references=[]))
    table = Table(db, provider, rng=SequenceRandomness(default=4))
    r = await table.send("! ร่าย hunters_mark")
    assert r.state_mutated                                       # concentration buff committed
    async with db.session() as s:
        eff = (await s.execute(select(ActiveEffect).where(
            ActiveEffect.character_id == world.kael_id,
            ActiveEffect.active.is_(True)))).scalars().all()
        assert any(e.spell_key == "hunters_mark" for e in eff)
