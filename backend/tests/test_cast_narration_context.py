"""Cast narration carries the bounded character packet (issue #1, item C — spell path).

A committed cast is no longer a bare "You cast X." The engine still owns every number
(the mechanical line is a SEPARATE object), but the SAME committed result is then
dramatized with the character's appearance and — for a DIVINE spell — the relevant,
PUBLIC faith (deity, sacred symbol). Faith is surfaced only when the moment is
religiously relevant, is never invented, and a SECRET belief never leaks.

Covers required scenarios: 1 (spell using appearance/backstory), 3 (missed attack),
9 (nothing relevant → nothing surfaced), 10 (no invented/secret facts).
"""
from __future__ import annotations

from app.core.randomness import SequenceRandomness
from app.discord_bridge import InboundMessage
from app.engine import build_bridge
from app.models.character import Character
from app.models.progression import CharacterSpell
from app.presentation import MessageKind
from app.schemas.belief import (
    BeliefProfile,
    BeliefSource,
    BeliefStance,
    BeliefVisibility,
    DevotionLevel,
)
from app.memory.character_context import build_character_narrative_context
from app.schemas.llm_io import ActionInterpretation, Narration
from app.services.beliefs import BeliefService
from app.services.faith import FaithService
from app.tabletop.combat import CombatantSpec, CombatService
from app.tabletop.dice import DiceEngine
from app.tabletop.resources import ResourceEngine
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1"):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"cn{_n['v']}", guild_id="g", channel_id="chan-1",
        author_discord_id=author, author_display_name="กี้", content=content,
    )


async def _make_caster(db, world, *, char_class, prepared=(), cantrips=()):
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        c.char_class = char_class
        c.int_score = c.wis_score = c.cha_score = 16
        c.con_score = 13
        for cn in cantrips:
            s.add(CharacterSpell(character_id=c.id, spell_key=cn, kind="cantrip"))
        for sp in prepared:
            s.add(CharacterSpell(character_id=c.id, spell_key=sp, kind="known", prepared=True))
        await ResourceEngine(s).grant(c, "resource:spell_slots_1")


async def _give_public_selune_faith(db, world, *, symbol, appearance="", secret=False):
    """Set a readable belief profile directly (bypassing cleric-mechanics validation,
    which is irrelevant to what the narrator reads). Returns the deity's Thai name."""
    async with db.unit_of_work() as s:
        await FaithService(s).activate_pantheon(world.campaign_id, "forgotten_realms")
        char = await s.get(Character, world.kael_id)
        if appearance:
            char.appearance = appearance
        profile = BeliefProfile(
            primary_deity_key="selune",
            stance=(BeliefStance.SECRET_BELIEVER if secret else BeliefStance.DEVOUT),
            devotion=DevotionLevel.DEVOUT,
            visibility=(BeliefVisibility.SECRET if secret else BeliefVisibility.PUBLIC),
            sacred_symbol=symbol,
            source=BeliefSource.PLAYER_AUTHORED, provenance="TEST",
        )
        char.belief_profile = BeliefService.encode(profile)
        deity = await FaithService(s).get_deity(world.campaign_id, "selune")
        return deity.name_th


async def _start_combat_with_guard(db, world, session_id, *, ac=14, hp=20):
    async with db.unit_of_work() as s:
        await CombatService(s, DiceEngine(SequenceRandomness(default=10))).start_combat(
            campaign_id=world.campaign_id, session_id=session_id,
            specs=[
                CombatantSpec(entity_ref=f"character:{world.kael_id}", name="Kael",
                              hp=12, max_hp=12, ac=14, is_pc=True),
                CombatantSpec(entity_ref=f"npc:{world.guard_npc_id}", name="ยามเฝ้าประตู",
                              hp=hp, max_hp=hp, ac=ac),
            ])


def _cast(reference, targets=()):
    return lambda m, model: ActionInterpretation(
        goal=f"ร่าย {reference}", method="ร่ายคาถา", intent_confidence=0.9,
        cast_intent=True, spell_reference=reference, target_references=list(targets))


# --- scenario 1: a divine spell weaves in faith + appearance ------------------

async def test_divine_cast_incorporates_public_faith_and_appearance(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    await _make_caster(db, world, char_class="cleric", prepared=("bless",))
    deity_name = await _give_public_selune_faith(
        db, world, symbol="สัญลักษณ์พระจันทร์เสี้ยวที่บิ่น",
        appearance="เกราะเกล็ดสีเงินบิ่นเป็นรอย ริบบิ้นแดงซีดผูกข้อมือ",
    )

    captured = {}

    def _cap(messages, model):
        captured["blob"] = "\n".join(m.get("content", "") for m in messages)
        return Narration(
            text="แสงจันทร์นวลสาดผ่านเกราะที่บิ่น มือของเจ้าแนบสัญลักษณ์เย็นเยียบ",
            decision_prompt="พรศักดิ์สิทธิ์ปกคลุมแล้ว — จะทำอะไรต่อ?")

    provider.on("interpret_committed_action", _cast("bless"))
    provider.on("generate_dm_narration", _cap)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))

    r = await bridge.handle_inbound(_msg("! ร่าย bless"))

    # MECHANICS and NARRATION are separate presentation objects.
    assert r.responses[0].kind == MessageKind.CHECK_RESOLUTION
    assert any(m.kind == MessageKind.SCENE_FRAME for m in r.responses)
    # The bounded packet carried the PUBLIC faith and the stored appearance.
    assert deity_name in captured["blob"]
    assert "สัญลักษณ์พระจันทร์เสี้ยวที่บิ่น" in captured["blob"]
    assert "เกราะเกล็ดสีเงินบิ่น" in captured["blob"]


# --- scenario 3: a missed attack spell is narrated AS a miss ------------------

async def test_missed_attack_spell_reaches_the_narrator_as_a_failure(db, provider):
    world = await build_world(db)
    sid, _ = await start_session_with_scene(db, world)
    await _make_caster(db, world, char_class="wizard", cantrips=("fire_bolt",))
    await _start_combat_with_guard(db, world, sid, ac=25)  # unreachable AC -> guaranteed miss

    captured = {}

    def _cap(messages, model):
        captured["blob"] = "\n".join(
            m.get("content", "") for m in messages if m.get("role") == "user")
        return Narration(text="ลำไฟพุ่งเฉียดไหล่ยาม กระทบกำแพงจนประกายไฟกระจาย",
                         decision_prompt="ยามหันขวับมาแล้ว — จะทำอะไรต่อ?")

    provider.on("interpret_committed_action", _cast("fire_bolt", ["ยามเฝ้าประตู"]))
    provider.on("generate_dm_narration", _cap)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=2))

    r = await bridge.handle_inbound(_msg("! ร่าย fire_bolt ใส่ ยามเฝ้าประตู"))

    assert r.responses[0].kind == MessageKind.CHECK_RESOLUTION
    # The committed miss — not a re-decided hit — is what the narrator was handed.
    assert "OUTCOME: failure" in captured["blob"]


# --- scenario 9: an ARCANE cast does not surface faith (relevance discipline) --

async def test_arcane_cast_does_not_surface_faith_even_when_a_belief_exists(db, provider):
    world = await build_world(db)
    sid, _ = await start_session_with_scene(db, world)
    await _make_caster(db, world, char_class="wizard", cantrips=("fire_bolt",))
    deity_name = await _give_public_selune_faith(db, world, symbol="จี้พระจันทร์เงิน")
    await _start_combat_with_guard(db, world, sid, ac=12)

    captured = {}

    def _cap(messages, model):
        captured["blob"] = "\n".join(
            m.get("content", "") for m in messages if m.get("role") == "user")
        return Narration(text="ลำไฟพุ่งเข้าใส่ยาม", decision_prompt="จะทำอะไรต่อ?")

    provider.on("interpret_committed_action", _cast("fire_bolt", ["ยามเฝ้าประตู"]))
    provider.on("generate_dm_narration", _cap)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=2))

    await bridge.handle_inbound(_msg("! ร่าย fire_bolt ใส่ ยามเฝ้าประตู"))

    # Faith is for divine acts, not every spell a believer casts.
    assert deity_name not in captured["blob"]
    assert "จี้พระจันทร์เงิน" not in captured["blob"]


# --- scenario 10: a SECRET belief never leaks, even on a divine cast ----------

async def test_secret_belief_never_leaks_into_divine_cast_narration(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    await _make_caster(db, world, char_class="cleric", prepared=("bless",))
    deity_name = await _give_public_selune_faith(
        db, world, symbol="สัญลักษณ์ที่ซ่อนไว้ใต้เสื้อ", secret=True)

    captured = {}

    def _cap(messages, model):
        captured["blob"] = "\n".join(
            m.get("content", "") for m in messages if m.get("role") == "user")
        return Narration(text="พรศักดิ์สิทธิ์แผ่ปกคลุม", decision_prompt="จะทำอะไรต่อ?")

    provider.on("interpret_committed_action", _cast("bless"))
    provider.on("generate_dm_narration", _cap)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))

    await bridge.handle_inbound(_msg("! ร่าย bless"))

    # A hidden faith is hidden — the table-facing packet must not carry it.
    assert deity_name not in captured["blob"]
    assert "สัญลักษณ์ที่ซ่อนไว้ใต้เสื้อ" not in captured["blob"]


# --- scenario 11: an explicit faith invocation on a NON-cast action surfaces the
#     PUBLIC deity, so the narrator can voice a grounded battle cry ("เพื่อ<เทพ>!") ---

async def test_faith_invocation_surfaces_public_deity_for_a_battle_cry(db, provider):
    world = await build_world(db)
    deity_name = await _give_public_selune_faith(db, world, symbol="จี้พระจันทร์เสี้ยว")
    async with db.session() as s:
        char = await s.get(Character, world.kael_id)
        ctx = await build_character_narrative_context(
            s, character=char,
            action_text="Kael ตะโกนด้วยศรัทธาแล้วนำเพื่อนบุกเข้าใส่ศัตรู",
            campaign_id=world.campaign_id,
        )
    # The god's name is available for the narrator to voice — read from canon, not invented.
    assert deity_name in (ctx.faith.get("deity") or "")
    assert deity_name in ctx.as_block()


async def test_plain_action_without_faith_words_does_not_surface_deity(db, provider):
    world = await build_world(db)
    await _give_public_selune_faith(db, world, symbol="จี้พระจันทร์เสี้ยว")
    async with db.session() as s:
        char = await s.get(Character, world.kael_id)
        ctx = await build_character_narrative_context(
            s, character=char,
            action_text="Kael เดินเงียบ ๆ ไปหลบหลังลังไม้",
            campaign_id=world.campaign_id,
        )
    # Faith is not surfaced on every ordinary turn a believer takes.
    assert ctx.faith == {}


async def test_secret_faith_never_surfaces_even_when_invoked(db, provider):
    world = await build_world(db)
    deity_name = await _give_public_selune_faith(
        db, world, symbol="สัญลักษณ์ที่ซ่อนไว้", secret=True)
    async with db.session() as s:
        char = await s.get(Character, world.kael_id)
        ctx = await build_character_narrative_context(
            s, character=char,
            action_text="Kael สวดภาวนาต่อเทพเจ้าอย่างเงียบ ๆ",
            campaign_id=world.campaign_id,
        )
    # An invocation cannot pry loose a SECRET belief — it stays hidden.
    assert ctx.faith == {}
    assert deity_name not in ctx.as_block()
