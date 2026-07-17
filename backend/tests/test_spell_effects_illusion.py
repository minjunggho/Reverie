"""Minor Illusion creates something that exists in the world.

The reported bug, exactly: the player casts Minor Illusion, Reverie replies with the
bare spell name ('ภาพลวงย่อม'), nothing is created, no NPC perceives anything, and
casting it again produces the same empty response.

The root cause was not narration: SpellDef could only express attack/save/damage/
healing, so an illusion resolved to NOTHING and the engine's own summary had only the
spell's name left to print. These tests pin the whole path — parse → validate →
create → persist → observe → describe → expire — through the production bridge.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import AdminBridge, InboundMessage, is_admin_command
from app.engine import build_bridge
from app.models.character import Character
from app.models.npc import NPC
from app.models.progression import ActiveEffect, CharacterSpell
from app.npcs.npc_service import NPCService
from app.npcs.observer_service import ObserverService
from app.schemas.llm_io import ActionInterpretation
from app.tabletop.dice import DiceEngine
from app.tabletop.effects import EffectService
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1", name="Neneko", mid=None):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=mid or f"mi{_n['v']}", guild_id="guild-1",
        channel_id="chan-1", author_discord_id=author,
        author_display_name=name, content=content,
    )


class Table:
    def __init__(self, db, provider, rng=None):
        self.game = build_bridge(db, provider=provider,
                                 rng=rng or SequenceRandomness(default=3))
        self.admin = AdminBridge(db, provider, creation_flow=self.game.creation_flow,
                                 session_zero=self.game.session_zero)

    async def send(self, content, author="disc-p1", name="Neneko", mid=None):
        inbound = _msg(content, author=author, name=name, mid=mid)
        if is_admin_command(content):
            return await self.admin.handle(inbound)
        return await self.game.handle_inbound(inbound)


async def _make_illusionist(db, world):
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        c.char_class = "wizard"
        c.int_score = 16
        s.add(CharacterSpell(character_id=c.id, spell_key="minor_illusion",
                             kind="cantrip"))


def _casts(provider, *, description: str, modes: list[str]):
    """The interpreter reports what the PLAYER asked for. It never decides whether
    the spell allows it — that is the engine's job, and these tests prove it."""
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="เบนความสนใจทุกคนในร้าน", method="ร่ายภาพลวง",
        intent_confidence=0.9, cast_intent=True,
        spell_reference="minor illusion", target_references=[],
        spell_description=description, spell_modes=modes))


async def _illusions(db, campaign_id, scene_id=None):
    async with db.session() as s:
        return await EffectService(s).world_effects_in(
            campaign_id=campaign_id, scene_id=scene_id)


# --- 1-3: a valid cast creates a real world effect ------------------------------

async def test_sound_illusion_creates_a_persisted_world_effect(db, provider):
    world = await build_world(db)
    await _make_illusionist(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    _casts(provider, description="เสียงแมวร้องจากมุมห้อง", modes=["sound"])
    table = Table(db, provider)

    await table.send("! ร่าย minor illusion เสียงแมวร้องที่มุมห้อง")

    rows = await _illusions(db, world.campaign_id, scene_id)
    assert len(rows) == 1
    effect = rows[0]
    assert effect.data["modes"] == ["sound"]
    assert effect.data["description"] == "เสียงแมวร้องจากมุมห้อง"
    assert effect.data["category"] == "illusion"
    # It is anchored in the world, which is what lets NPCs and later turns find it.
    assert effect.location_id == world.location_id
    assert effect.scene_id == scene_id
    assert effect.character_id == world.kael_id      # the creator is recorded


async def test_visual_illusion_creates_a_persisted_world_effect(db, provider):
    world = await build_world(db)
    await _make_illusionist(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    _casts(provider, description="แมวอ้วนกำลังเต้น", modes=["image"])
    table = Table(db, provider)

    await table.send("! ร่าย minor illusion ภาพแมวอ้วนกำลังเต้น")

    rows = await _illusions(db, world.campaign_id, scene_id)
    assert len(rows) == 1 and rows[0].data["modes"] == ["image"]


async def test_the_illusion_has_a_duration_and_a_detection_contract(db, provider):
    world = await build_world(db)
    await _make_illusionist(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    _casts(provider, description="แมวอ้วน", modes=["image"])
    await Table(db, provider).send("! ร่าย minor illusion")

    effect = (await _illusions(db, world.campaign_id, scene_id))[0]
    assert effect.duration_minutes == 1                    # SRD: 1 minute
    assert effect.data["detect_skill"] == "investigation"
    assert effect.data["insubstantial"] is True
    assert effect.data["investigated"] is False
    assert effect.data["discovered_by"] == []


# --- 4-5: who can and cannot react ----------------------------------------------

async def test_npcs_present_can_react_to_the_illusion(db, provider):
    world = await build_world(db)
    await _make_illusionist(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    _casts(provider, description="แมวอ้วนกำลังเต้น", modes=["image"])

    result = await Table(db, provider).send("! ร่าย minor illusion")

    effect = (await _illusions(db, world.campaign_id, scene_id))[0]
    observers = effect.data["observers"]
    assert [o["npc_name"] for o in observers] == ["ยามเฝ้าประตู"]
    assert observers[0]["noticed"] is True
    # The player is told who noticed — the distraction is only real if someone saw it.
    body = "\n".join(r.content for r in result.responses)
    assert "ยามเฝ้าประตู" in body


async def test_an_npc_elsewhere_does_not_react(db, provider):
    """Co-location is the distance model: an NPC in another room cannot see a
    five-foot illusion in this one."""
    world = await build_world(db)
    await _make_illusionist(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        await NPCService(s).create_npc(
            campaign_id=world.campaign_id, name="คนสวนที่อยู่ไกล",
            current_location_id=None)      # not at the scene's location
    _casts(provider, description="แมวอ้วน", modes=["image"])

    await Table(db, provider).send("! ร่าย minor illusion")

    effect = (await _illusions(db, world.campaign_id, scene_id))[0]
    names = [o["npc_name"] for o in effect.data["observers"]]
    assert "คนสวนที่อยู่ไกล" not in names
    assert names == ["ยามเฝ้าประตู"]


async def test_an_unavailable_npc_does_not_react(db, provider):
    world = await build_world(db)
    await _make_illusionist(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        guard = await s.get(NPC, world.guard_npc_id)
        guard.available = False           # e.g. left, or otherwise out of the scene
    _casts(provider, description="แมวอ้วน", modes=["image"])

    await Table(db, provider).send("! ร่าย minor illusion")

    effect = (await _illusions(db, world.campaign_id, scene_id))[0]
    assert effect.data["observers"] == []


# --- 6: investigation can reveal it ---------------------------------------------

async def test_investigation_can_reveal_the_illusion(db, provider):
    """Noticing is not disbelieving. An NPC only sees through it by deliberately
    investigating, and then the effect remembers who is no longer fooled."""
    world = await build_world(db)
    await _make_illusionist(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    _casts(provider, description="แมวอ้วน", modes=["image"])
    await Table(db, provider).send("! ร่าย minor illusion")

    effect_id = (await _illusions(db, world.campaign_id, scene_id))[0].id
    async with db.unit_of_work() as s:
        effect = await s.get(ActiveEffect, effect_id)
        guard = await s.get(NPC, world.guard_npc_id)
        # A 19 beats any reasonable DC; the roll is the dice engine's, not the model's.
        observer = ObserverService(s, DiceEngine(SequenceRandomness([19], default=19)))
        seen = await observer.investigate(
            campaign_id=world.campaign_id, effect=effect, npc=guard, dc=13)
        assert seen.saw_through is True
        assert seen.check["total"] >= 13

    async with db.session() as s:
        effect = await s.get(ActiveEffect, effect_id)
        assert effect.data["investigated"] is True
        assert effect.data["discovered_by"] == [f"npc:{world.guard_npc_id}"]
        assert ObserverService.is_fooled(effect, world.guard_npc_id) is False


async def test_a_failed_investigation_leaves_the_npc_fooled(db, provider):
    world = await build_world(db)
    await _make_illusionist(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    _casts(provider, description="แมวอ้วน", modes=["image"])
    await Table(db, provider).send("! ร่าย minor illusion")

    effect_id = (await _illusions(db, world.campaign_id, scene_id))[0].id
    async with db.unit_of_work() as s:
        effect = await s.get(ActiveEffect, effect_id)
        guard = await s.get(NPC, world.guard_npc_id)
        observer = ObserverService(s, DiceEngine(SequenceRandomness([2], default=2)))
        seen = await observer.investigate(
            campaign_id=world.campaign_id, effect=effect, npc=guard, dc=13)
        assert seen.saw_through is False

    async with db.session() as s:
        effect = await s.get(ActiveEffect, effect_id)
        assert effect.data["discovered_by"] == []
        assert ObserverService.is_fooled(effect, world.guard_npc_id) is True


# --- 7: expiry -------------------------------------------------------------------

async def test_the_illusion_expires(db, provider):
    world = await build_world(db)
    await _make_illusionist(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    _casts(provider, description="แมวอ้วน", modes=["image"])
    await Table(db, provider).send("! ร่าย minor illusion")

    async with db.session() as s:
        service = EffectService(s)
        assert await service.world_effects_in(
            campaign_id=world.campaign_id, scene_id=scene_id, game_time=0)
        # 1 minute later it is gone from the read path even before any sweep runs.
        assert await service.world_effects_in(
            campaign_id=world.campaign_id, scene_id=scene_id, game_time=1) == []

    async with db.unit_of_work() as s:
        ended = await EffectService(s).expire_due(
            campaign_id=world.campaign_id, game_time=2, session_id=sid)
    assert [e.spell_key for e in ended] == ["minor_illusion"]
    assert await _illusions(db, world.campaign_id, scene_id) == []


# --- 8: invalid parameters are corrected, and explained -------------------------

async def test_asking_for_image_and_sound_keeps_one_and_says_why(db, provider):
    """The exact request from the screenshot: a cat that both dances AND sings. The
    SRD allows an image OR a sound. The engine must not silently accept it, and must
    not simply refuse — it keeps the closest valid version and explains."""
    world = await build_world(db)
    await _make_illusionist(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    _casts(provider, description="แมวอ้วนกำลังเต้นและร้องเพลง",
           modes=["image", "sound"])

    result = await Table(db, provider).send(
        "! ร่าย minor illusion สร้างภาพลวงตาเป็นแมวอ้วนกำลังเต้นและร้องเพลง")

    effect = (await _illusions(db, world.campaign_id, scene_id))[0]
    assert effect.data["modes"] == ["image"], "only one form may survive"
    body = "\n".join(r.content for r in result.responses)
    assert "อย่างใดอย่างหนึ่ง" in body, f"the limit must be explained: {body!r}"
    assert "แมวอ้วนกำลังเต้นและร้องเพลง" in body    # what they asked for is kept


async def test_an_unparseable_mode_still_produces_a_valid_illusion(db, provider):
    """A cast with no usable form must not create a formless effect that nobody can
    perceive — it falls back to the spell's first declared mode."""
    world = await build_world(db)
    await _make_illusionist(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    _casts(provider, description="อะไรสักอย่าง", modes=[])

    await Table(db, provider).send("! ร่าย minor illusion")

    effect = (await _illusions(db, world.campaign_id, scene_id))[0]
    assert effect.data["modes"] == ["image"]


# --- 9: later turns can find it ---------------------------------------------------

async def test_a_later_turn_can_reference_the_existing_illusion(db, provider):
    """The illusion is still there on the next turn — that is what makes it part of
    the scene rather than a one-off line of prose."""
    world = await build_world(db)
    await _make_illusionist(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    _casts(provider, description="แมวอ้วนกำลังเต้น", modes=["image"])
    table = Table(db, provider)
    await table.send("! ร่าย minor illusion")

    # An ordinary, unrelated action happens.
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="เดินไปที่บาร์", method="เดิน", intent_confidence=0.9,
        target_references=[]))
    await table.send("! เดินไปที่บาร์")

    still = await _illusions(db, world.campaign_id, scene_id)
    assert len(still) == 1
    assert still[0].data["description"] == "แมวอ้วนกำลังเต้น"


# --- 10: never a placeholder ------------------------------------------------------

async def test_the_response_is_never_the_bare_spell_name(db, provider):
    """The precise regression: the whole reply used to be 'ภาพลวงย่อม'."""
    world = await build_world(db)
    await _make_illusionist(db, world)
    await start_session_with_scene(db, world)
    _casts(provider, description="แมวอ้วนกำลังเต้น", modes=["image"])

    result = await Table(db, provider).send("! ร่าย minor illusion")

    body = "\n".join(r.content for r in result.responses).strip()
    assert body != "ภาพลวงย่อม", "this is the original bug"
    assert "แมวอ้วนกำลังเต้น" in body, (
        f"the reply must describe what was actually created; got {body!r}")


async def test_repeating_the_cast_creates_a_second_distinct_illusion(db, provider):
    """'Repeating the command gives almost the same empty response' — now each cast
    creates its own effect with its own content."""
    world = await build_world(db)
    await _make_illusionist(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    table = Table(db, provider)

    _casts(provider, description="แมวอ้วนกำลังเต้น", modes=["image"])
    await table.send("! ร่าย minor illusion แมว")
    _casts(provider, description="เสียงจานแตก", modes=["sound"])
    await table.send("! ร่าย minor illusion เสียงจานแตก")

    rows = await _illusions(db, world.campaign_id, scene_id)
    assert len(rows) == 2
    assert {tuple(r.data["modes"]) for r in rows} == {("image",), ("sound",)}
    assert {r.data["description"] for r in rows} == {"แมวอ้วนกำลังเต้น", "เสียงจานแตก"}
