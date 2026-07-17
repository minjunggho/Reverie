"""Part 1 — real CAST actions travel the committed-action pipeline into SpellEngine.

`! ร่าย <spell> ใส่ <target>` → interpreter (cast_intent) → engine resolves the
spell (authoritative resolver, against the caster's OWN pool) + targets (scene) +
stats (Combatant/Character) → SpellEngine.cast → atomic commit → narration. The
LLM never decides the mechanical outcome. Drives the production bridge, not the
engine directly.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import AdminBridge, InboundMessage, is_admin_command
from app.engine import build_bridge
from app.models.character import Character
from app.models.combat import Combatant
from app.models.progression import ActiveEffect, CharacterSpell, ResourceState
from app.presentation import MessageKind
from app.tabletop.combat import CombatantSpec, CombatService
from app.tabletop.dice import DiceEngine
from app.tabletop.resources import ResourceEngine
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1", name="กี้", mid=None):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=mid or f"sp{_n['v']}", guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name=name, content=content,
    )


class Table:
    def __init__(self, db, provider, rng=None):
        self.game = build_bridge(db, provider=provider,
                                 rng=rng or SequenceRandomness(default=10))
        self.admin = AdminBridge(db, provider, creation_flow=self.game.creation_flow,
                                 session_zero=self.game.session_zero)

    async def send(self, content, author="disc-p1", name="กี้", mid=None):
        inbound = _msg(content, author=author, name=name, mid=mid)
        if is_admin_command(content):
            return await self.admin.handle(inbound)
        return await self.game.handle_inbound(inbound)


async def _make_caster(db, world, *, char_class="wizard", cantrips=("fire_bolt",),
                       prepared=("magic_missile",), con=13):
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        c.char_class = char_class
        c.int_score = c.wis_score = c.cha_score = 16
        c.con_score = con
        for cn in cantrips:
            s.add(CharacterSpell(character_id=c.id, spell_key=cn, kind="cantrip"))
        for sp in prepared:
            kind = "book" if char_class == "wizard" else "known"
            s.add(CharacterSpell(character_id=c.id, spell_key=sp, kind=kind, prepared=True))
        await ResourceEngine(s).grant(c, "resource:spell_slots_1")


async def _start_combat_with_guard(db, world, session_id, *, ac=12, hp=20):
    async with db.unit_of_work() as s:
        dice = DiceEngine(SequenceRandomness(default=10))
        await CombatService(s, dice).start_combat(
            campaign_id=world.campaign_id, session_id=session_id,
            specs=[
                CombatantSpec(entity_ref=f"character:{world.kael_id}", name="Kael",
                              hp=9, max_hp=9, ac=14, is_pc=True),
                CombatantSpec(entity_ref=f"npc:{world.guard_npc_id}", name="ยามเฝ้าประตู",
                              hp=hp, max_hp=hp, ac=ac),
            ])


async def _slot_current(db, char_id):
    async with db.session() as s:
        st = (await s.execute(select(ResourceState).where(
            ResourceState.character_id == char_id,
            ResourceState.resource_id == "resource:spell_slots_1"))).scalar_one()
        return st.current


async def _guard_hp(db, world):
    async with db.session() as s:
        cb = (await s.execute(select(Combatant).where(
            Combatant.entity_ref == f"npc:{world.guard_npc_id}"))).scalars().first()
        return cb.hp if cb else None


# --- spell name resolution (Thai / English / alias / hyphen / underscore) -------

@pytest.mark.parametrize("form", ["ลูกไฟพุ่ง", "Fire Bolt", "fire_bolt", "fire-bolt", "fire bolt"])
async def test_spell_name_forms_all_resolve_through_the_authoritative_resolver(db, provider, form):
    world = await build_world(db)
    await _make_caster(db, world)
    sid, _ = await start_session_with_scene(db, world)
    await _start_combat_with_guard(db, world, sid)
    table = Table(db, provider, rng=SequenceRandomness([18, 6]))   # attack 18 hit, 6 dmg
    r = await table.send(f"! ร่าย {form} ใส่ ยามเฝ้าประตู")
    assert r.responses[0].kind == MessageKind.CHECK_RESOLUTION
    assert "ลูกไฟพุ่ง" in (r.responses[0].title or "")


async def test_invalid_spell_name_gives_suggestions_and_casts_nothing(db, provider):
    world = await build_world(db)
    await _make_caster(db, world)
    sid, _ = await start_session_with_scene(db, world)
    await _start_combat_with_guard(db, world, sid)
    table = Table(db, provider)
    r = await table.send("! ร่าย ลูกไฟระเบิดจักรวาล ใส่ ยามเฝ้าประตู")
    assert r.responses[0].kind == MessageKind.TABLE_NOTICE
    assert "ไม่มีคาถา" in r.responses[0].content
    assert await _slot_current(db, world.kael_id) == 2          # nothing consumed


# --- target resolution ----------------------------------------------------------

async def test_targeted_spell_without_a_target_asks_one_clarification(db, provider):
    world = await build_world(db)
    await _make_caster(db, world)
    sid, _ = await start_session_with_scene(db, world)
    table = Table(db, provider)
    # Force a cast with no target reference.
    from app.schemas.llm_io import ActionInterpretation
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ร่ายไฟ", method="ร่าย", intent_confidence=0.9,
        cast_intent=True, spell_reference="fire_bolt", target_references=[]))
    r = await table.send("! ร่าย fire bolt")
    assert "ใส่ใคร" in r.responses[0].content


async def test_attack_spell_uses_stored_target_ac_and_persists_damage(db, provider):
    world = await build_world(db)
    await _make_caster(db, world)
    sid, _ = await start_session_with_scene(db, world)
    await _start_combat_with_guard(db, world, sid, ac=12, hp=20)
    # server d20=11 (+attack). Wizard INT16(+3) PB2 -> +5 -> 16 vs AC12 = hit. dmg=7.
    table = Table(db, provider, rng=SequenceRandomness([11, 7]))
    r = await table.send("! ร่าย fire_bolt ใส่ ยามเฝ้าประตู")
    assert r.responses[0].kind == MessageKind.CHECK_RESOLUTION
    assert "โจมตีเวท" in r.responses[0].content and "โดน" in r.responses[0].content
    assert await _guard_hp(db, world) == 13                      # 20 - 7 committed


async def test_save_spell_uses_stored_target_and_persists_half_on_success(db, provider):
    world = await build_world(db)
    # burning_hands: DEX save, half on save. Target = the OTHER PC (has a Character,
    # so authoritative AC + save mods exist without combat).
    await _make_caster(db, world, cantrips=("fire_bolt",), prepared=("burning_hands",))
    sid, scene_id = await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        bront = await s.get(Character, world.bront_id)
        bront.hp = 13
    from app.schemas.llm_io import ActionInterpretation
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ร่ายไฟ", method="ร่าย", intent_confidence=0.9, cast_intent=True,
        spell_reference="burning_hands", target_references=["Bront"]))
    # damage 2+2+2=6; save roll nat 20 -> passes -> half = 3.
    table = Table(db, provider, rng=SequenceRandomness([2, 2, 2, 20]))
    r = await table.send("! ร่าย burning_hands ใส่ Bront")
    assert "เซฟ" in r.responses[0].content
    async with db.session() as s:
        assert (await s.get(Character, world.bront_id)).hp == 10   # 13 - 3


# --- healing / concentration / slots --------------------------------------------

async def test_healing_spell_persists_and_targets_the_caster(db, provider):
    world = await build_world(db)
    await _make_caster(db, world, char_class="cleric",
                       cantrips=("sacred_flame",), prepared=("cure_wounds",))
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        c.hp = 2
    sid, _ = await start_session_with_scene(db, world)
    from app.schemas.llm_io import ActionInterpretation
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="รักษา", method="ร่าย", intent_confidence=0.9, cast_intent=True,
        spell_reference="cure_wounds", target_references=[]))
    table = Table(db, provider, rng=SequenceRandomness(default=5))
    r = await table.send("! ร่าย cure_wounds")
    assert "ฟื้น" in r.responses[0].content
    async with db.session() as s:
        assert (await s.get(Character, world.kael_id)).hp > 2     # healed, committed
        assert await _slot_current(db, world.kael_id) == 1         # a slot was spent once


async def test_concentration_effect_persists_and_second_replaces_first(db, provider):
    world = await build_world(db)
    await _make_caster(db, world, char_class="cleric",
                       cantrips=("sacred_flame",), prepared=("bless", "shield_of_faith"))
    sid, _ = await start_session_with_scene(db, world)
    from app.schemas.llm_io import ActionInterpretation
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="อวยพร", method="ร่าย", intent_confidence=0.9, cast_intent=True,
        spell_reference="bless", target_references=[]))
    table = Table(db, provider, rng=SequenceRandomness(default=4))
    await table.send("! ร่าย bless")
    # A concentration spell writes TWO kinds of row: the concentration marker (what
    # the caster is holding — at most one) and the effect(s) it sustains (Bless can
    # bless several allies from one concentration). Asserting a bare row count would
    # conflate the two, so each is checked for what it means.
    async with db.session() as s:
        conc = (await s.execute(select(ActiveEffect).where(
            ActiveEffect.character_id == world.kael_id,
            ActiveEffect.requires_concentration.is_(True),
            ActiveEffect.active.is_(True)))).scalars().all()
        assert len(conc) == 1 and conc[0].spell_key == "bless"
        buffs = (await s.execute(select(ActiveEffect).where(
            ActiveEffect.spell_key == "bless", ActiveEffect.kind == "roll_bonus",
            ActiveEffect.active.is_(True)))).scalars().all()
        assert len(buffs) == 1, "bless must grant a real, queryable roll bonus"
    # A second concentration spell replaces the first (SRD: one at a time) — and the
    # effects the first was sustaining end with it, or a dropped Bless would keep
    # handing out dice forever.
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ปกป้อง", method="ร่าย", intent_confidence=0.9, cast_intent=True,
        spell_reference="shield_of_faith", target_references=[]))
    await table.send("! ร่าย shield_of_faith")
    async with db.session() as s:
        conc = (await s.execute(select(ActiveEffect).where(
            ActiveEffect.character_id == world.kael_id,
            ActiveEffect.requires_concentration.is_(True),
            ActiveEffect.active.is_(True)))).scalars().all()
        assert len(conc) == 1 and conc[0].spell_key == "shield_of_faith"
        stale = (await s.execute(select(ActiveEffect).where(
            ActiveEffect.spell_key == "bless",
            ActiveEffect.active.is_(True)))).scalars().all()
        assert stale == [], "replacing concentration must end the effects it held up"


async def test_leveled_spell_consumes_exactly_one_slot(db, provider):
    world = await build_world(db)
    await _make_caster(db, world, prepared=("magic_missile",))
    sid, _ = await start_session_with_scene(db, world)
    await _start_combat_with_guard(db, world, sid)
    table = Table(db, provider, rng=SequenceRandomness(default=3))
    from app.schemas.llm_io import ActionInterpretation
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ยิงมิสไซล์", method="ร่าย", intent_confidence=0.9, cast_intent=True,
        spell_reference="magic_missile", target_references=["ยาม"]))
    assert await _slot_current(db, world.kael_id) == 2
    await table.send("! ร่าย magic_missile ใส่ ยาม")
    assert await _slot_current(db, world.kael_id) == 1           # exactly one


async def test_casting_an_unprepared_spell_consumes_nothing(db, provider):
    world = await build_world(db)
    await _make_caster(db, world, prepared=("magic_missile",))
    sid, _ = await start_session_with_scene(db, world)
    await _start_combat_with_guard(db, world, sid)
    table = Table(db, provider)
    from app.schemas.llm_io import ActionInterpretation
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ร่าย", method="ร่าย", intent_confidence=0.9, cast_intent=True,
        spell_reference="shield", target_references=["ยาม"]))   # not prepared
    r = await table.send("! ร่าย shield ใส่ ยาม")
    assert r.responses[0].kind == MessageKind.TABLE_NOTICE       # not in pool -> diagnostic
    assert await _slot_current(db, world.kael_id) == 2           # nothing spent


async def test_duplicate_discord_message_casts_only_once(db, provider):
    world = await build_world(db)
    await _make_caster(db, world, prepared=("magic_missile",))
    sid, _ = await start_session_with_scene(db, world)
    await _start_combat_with_guard(db, world, sid)
    table = Table(db, provider, rng=SequenceRandomness(default=3))
    from app.schemas.llm_io import ActionInterpretation
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ยิง", method="ร่าย", intent_confidence=0.9, cast_intent=True,
        spell_reference="magic_missile", target_references=["ยาม"]))
    await table.send("! ร่าย magic_missile ใส่ ยาม", mid="dup-cast")
    r2 = await table.send("! ร่าย magic_missile ใส่ ยาม", mid="dup-cast")   # same id
    assert r2.duplicate is True
    assert await _slot_current(db, world.kael_id) == 1           # only one cast


async def test_spell_effects_and_resources_survive_restart(tmp_path, provider):
    from app.db.session import Database

    url = f"sqlite+aiosqlite:///{(tmp_path / 'cast-restart.db').as_posix()}"
    first = Database(url, echo=False)
    await first.create_all()
    try:
        world = await build_world(first)
        await _make_caster(first, world, char_class="cleric",
                           cantrips=("sacred_flame",), prepared=("bless",))
        sid, _ = await start_session_with_scene(first, world)
        from app.schemas.llm_io import ActionInterpretation
        provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
            goal="อวยพร", method="ร่าย", intent_confidence=0.9, cast_intent=True,
            spell_reference="bless", target_references=[]))
        table = Table(first, provider, rng=SequenceRandomness(default=4))
        await table.send("! ร่าย bless")
    finally:
        await first.dispose()

    restarted = Database(url, echo=False)
    try:
        async with restarted.session() as s:
            conc = (await s.execute(select(ActiveEffect).where(
                ActiveEffect.character_id == world.kael_id,
                ActiveEffect.requires_concentration.is_(True),
                ActiveEffect.active.is_(True)))).scalars().all()
            assert len(conc) == 1 and conc[0].spell_key == "bless"  # effect persisted
            slot = (await s.execute(select(ResourceState).where(
                ResourceState.character_id == world.kael_id,
                ResourceState.resource_id == "resource:spell_slots_1"))).scalar_one()
            assert slot.current == slot.max_value - 1              # slot spend persisted

            # The BUFF itself — not merely some row — must come back usable: a worker
            # restart that loses the die is indistinguishable to the player from the
            # bug where the die never existed.
            from app.tabletop.effects import EffectService

            grants = await EffectService(s).bonus_grants_for(
                campaign_id=world.campaign_id,
                subject_ref=f"character:{world.kael_id}",
                roll_type="attack_roll", ability="str")
            assert [g.expression for g in grants] == ["1d4"]
    finally:
        await restarted.dispose()


# --- per-class: wizard / bard / cleric / ranger all cast through one path --------

@pytest.mark.parametrize("char_class,leveled,targeted", [
    ("wizard", "magic_missile", True),     # auto-hit force
    ("bard", "healing_word", False),       # heal (self)
    ("cleric", "guiding_bolt", True),      # ranged spell attack
    ("ranger", "hunters_mark", False),     # concentration self-buff
])
async def test_each_selectable_caster_class_casts_through_the_pipeline(
        db, provider, char_class, leveled, targeted):
    world = await build_world(db)
    await _make_caster(db, world, char_class=char_class, cantrips=(), prepared=(leveled,))
    sid, _ = await start_session_with_scene(db, world)
    await _start_combat_with_guard(db, world, sid)
    from app.schemas.llm_io import ActionInterpretation
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ร่าย", method="ร่าย", intent_confidence=0.9, cast_intent=True,
        spell_reference=leveled,
        target_references=["ยามเฝ้าประตู"] if targeted else []))
    table = Table(db, provider, rng=SequenceRandomness(default=3))   # valid for every die
    r = await table.send(f"! ร่าย {leveled}")
    # Each selectable caster reaches a committed mechanical result through the
    # ONE pipeline path — a slot spent, an effect, or damage/healing.
    assert r.state_mutated is True
    assert r.responses[0].kind == MessageKind.CHECK_RESOLUTION


async def test_discord_smoke_cast_through_reverie_client(db, provider):
    """Discord-level smoke: a cast travels the REAL ReverieClient routing/delivery
    (_route/_deliver/_send_one) — the same code the gateway calls — not just the
    bridge. No live gateway (no token/guild), so this is IMPLEMENTED_UNVERIFIED for
    a true gateway; it does prove the production callback path resolves a cast."""
    from discord_bot.client import ReverieClient
    from tests.test_production_discord_callbacks import FakeChannel

    world = await build_world(db)
    await _make_caster(db, world, prepared=("magic_missile",))
    sid, _ = await start_session_with_scene(db, world)
    await _start_combat_with_guard(db, world, sid)
    game = build_bridge(db, provider=provider, rng=SequenceRandomness(default=3))
    admin = AdminBridge(db, provider, creation_flow=game.creation_flow,
                        session_zero=game.session_zero)
    client = ReverieClient(bridge=game, admin=admin)
    channel = FakeChannel()
    from app.schemas.llm_io import ActionInterpretation
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ยิง", method="ร่าย", intent_confidence=0.9, cast_intent=True,
        spell_reference="magic_missile", target_references=["ยามเฝ้าประตู"]))
    inbound = _msg("! ร่าย magic_missile ใส่ ยามเฝ้าประตู", mid="smoke-cast")
    result = await client._route(inbound)
    await client._deliver(channel, result)
    assert result.state_mutated is True
    assert channel.sent and channel.sent[-1]["embed"] is not None   # rendered + delivered


async def test_sorcerer_and_warlock_now_reach_the_cast_path(db, provider):
    """Sorcerer/Warlock are unlocked (Part 3); their end-to-end create → cast is
    proven in tests/test_unlock_sorcerer_warlock.py through this same pipeline."""
    from app.tabletop.rules.core import SUPPORTED_CLASSES

    assert "sorcerer" in SUPPORTED_CLASSES and "warlock" in SUPPORTED_CLASSES
