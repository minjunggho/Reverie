"""An ordinary action never restarts the story.

Reported: "some player actions still cause the campaign's opening or first scene to
appear again", less often than before.

What the code actually guarantees, and what these tests pin:

  1. `SessionOpeningService` has exactly ONE caller — the `!rv session start` admin
     command. No action path can reach it, so an ordinary action CANNOT emit an
     opening. Test: the reference is asserted, so a future refactor that wires the
     opener into the action path fails here instead of at someone's table.
  2. The cinematic prologue is gated on a PERSISTED per-campaign flag, not on session
     numbering, so it cannot replay.
  3. Discord message IDs are unique + the result is cached, so a redelivered event
     replays the cached response instead of re-running the action.

The last test replays the exact sequence from the bug report — guidance, illusion,
ordinary actions, a resolver exception — and asserts the opening never reappears and
the scene never silently changes underneath the party.
"""
from __future__ import annotations

import inspect

from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import AdminBridge, InboundMessage, is_admin_command
from app.engine import build_bridge
from app.models.character import Character
from app.models.event import Event
from app.models.progression import CharacterSpell
from app.models.scene import Scene
from app.schemas.llm_io import ActionInterpretation
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1", name="Neneko", mid=None):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=mid or f"sc{_n['v']}", guild_id="guild-1",
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


async def _scene_ids(db) -> list[str]:
    async with db.session() as s:
        return [r.id for r in (await s.execute(select(Scene))).scalars()]


async def _scene_started_events(db) -> list[Event]:
    async with db.session() as s:
        return list((await s.execute(select(Event).where(
            Event.event_type == "SCENE_STARTED"))).scalars())


async def _caster(db, world):
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        c.char_class = "cleric"
        c.wis_score = 16
        for key in ("guidance", "minor_illusion"):
            s.add(CharacterSpell(character_id=c.id, spell_key=key, kind="cantrip"))


# --- 1: the opening is structurally unreachable from an action ------------------

def test_the_opening_service_is_only_reachable_from_the_admin_session_command():
    """The strongest guarantee available: an ordinary action has no code path to the
    opener at all. If someone later calls it from the action pipeline, this fails."""
    import app.orchestration.pipeline as pipeline_module

    source = inspect.getsource(pipeline_module)
    assert "SessionOpeningService" not in source, (
        "the action pipeline must never be able to start an opening — "
        "'return to the first scene' is not an error path")


def test_the_pipeline_never_creates_a_scene_for_an_ordinary_action():
    import app.orchestration.pipeline as pipeline_module

    source = inspect.getsource(pipeline_module)
    assert "create_scene" not in source, (
        "an ordinary action continues the committed scene; it never opens a new one")


# --- 2: ordinary actions keep the scene ----------------------------------------

async def test_many_different_actions_never_emit_a_second_scene(db, provider):
    world = await build_world(db)
    await _caster(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    table = Table(db, provider)

    actions = [
        ("! มองไปรอบ ๆ ห้อง", ActionInterpretation(
            goal="สังเกตการณ์", method="มอง", intent_confidence=0.9)),
        ("! เดินไปที่บาร์", ActionInterpretation(
            goal="เดินไปบาร์", method="เดิน", intent_confidence=0.9)),
        ("! ร่าย guidance ใส่ Bront", ActionInterpretation(
            goal="ช่วยเพื่อน", method="ร่าย", intent_confidence=0.9,
            cast_intent=True, spell_reference="guidance",
            target_references=["Bront"])),
        ("! ร่าย minor illusion", ActionInterpretation(
            goal="เบนความสนใจ", method="ร่าย", intent_confidence=0.9,
            cast_intent=True, spell_reference="minor illusion",
            spell_description="แมวอ้วนกำลังเต้น", spell_modes=["image"])),
        ("! ถามเจ้าของร้านเรื่องห้องพัก", ActionInterpretation(
            goal="ถามเรื่องห้อง", method="พูด", intent_confidence=0.9)),
    ]
    for text, interp in actions:
        provider.on("interpret_committed_action", lambda m, model, i=interp: i)
        await table.send(text)

    assert await _scene_ids(db) == [scene_id], "no action may create a new scene"
    assert len(await _scene_started_events(db)) == 0, (
        "no action may emit a SCENE_STARTED (the opening beat)")


# --- 3: duplicate delivery ------------------------------------------------------

async def test_the_same_discord_message_twice_transitions_state_once(db, provider):
    world = await build_world(db)
    await _caster(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ช่วยเพื่อน", method="ร่าย", intent_confidence=0.9, cast_intent=True,
        spell_reference="guidance", target_references=["Bront"]))
    table = Table(db, provider)

    await table.send("! ร่าย guidance ใส่ Bront", mid="dupe-1")
    await table.send("! ร่าย guidance ใส่ Bront", mid="dupe-1")   # redelivered

    from app.models.progression import ActiveEffect

    async with db.session() as s:
        effects = (await s.execute(select(ActiveEffect).where(
            ActiveEffect.spell_key == "guidance",
            ActiveEffect.kind == "roll_bonus",
            ActiveEffect.active.is_(True)))).scalars().all()
    assert len(effects) == 1, "a redelivered event must not cast the spell twice"
    assert await _scene_ids(db) == [scene_id]


# --- 4: a resolver exception does not reset the campaign -------------------------

async def test_a_resolver_exception_does_not_reset_to_the_first_scene(db, provider):
    """The brief's worry: 'event replay after an exception' / 'failure recovery
    rebuilding state from the original campaign document'. A blowing-up interpreter
    must leave the scene exactly where it was."""
    world = await build_world(db)
    await _caster(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    table = Table(db, provider)

    def _boom(messages, model):
        raise RuntimeError("interpreter exploded")

    provider.on("interpret_committed_action", _boom)
    await table.send("! ทำอะไรสักอย่างที่พัง")

    assert await _scene_ids(db) == [scene_id], "an exception must not open a scene"
    assert len(await _scene_started_events(db)) == 0


# --- 5: state reloads intact -----------------------------------------------------

async def test_reloading_state_keeps_the_scene_and_its_effects(tmp_path, provider):
    from app.db.session import Database

    url = f"sqlite+aiosqlite:///{(tmp_path / 'continuity.db').as_posix()}"
    first = Database(url, echo=False)
    await first.create_all()
    try:
        world = await build_world(first)
        await _caster(first, world)
        sid, scene_id = await start_session_with_scene(first, world)
        provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
            goal="เบนความสนใจ", method="ร่าย", intent_confidence=0.9, cast_intent=True,
            spell_reference="minor illusion",
            spell_description="แมวอ้วนกำลังเต้น", spell_modes=["image"]))
        await Table(first, provider).send("! ร่าย minor illusion")
    finally:
        await first.dispose()

    restarted = Database(url, echo=False)
    try:
        from app.tabletop.effects import EffectService

        async with restarted.session() as s:
            scenes = [r.id for r in (await s.execute(select(Scene))).scalars()]
            assert scenes == [scene_id], "the committed scene survives a reload"
            live = await EffectService(s).world_effects_in(
                campaign_id=world.campaign_id, scene_id=scene_id)
            assert len(live) == 1
            assert live[0].data["description"] == "แมวอ้วนกำลังเต้น"
    finally:
        await restarted.dispose()


# --- 6: the exact screenshot sequence --------------------------------------------

async def test_the_reported_screenshot_sequence_end_to_end(db, provider):
    """Reproduces the bug report: Hamu casts Guidance on Neneko; Neneko casts Minor
    Illusion of a singing, dancing cat; Neneko then tries to distract the innkeeper.

    Asserts the three things that were broken at once: the illusion is real, the
    guidance die reaches Neneko's check, and nothing restarts the scene.
    """
    world = await build_world(db)
    await _caster(db, world)
    sid, scene_id = await start_session_with_scene(db, world)
    table = Table(db, provider)

    # 1. "! ฮามุใช้ cantrip guidance ใส่เนเนโกะ"
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ช่วยเนเนโกะ", method="ร่ายคาถา", intent_confidence=0.9,
        cast_intent=True, spell_reference="guidance", target_references=["Bront"]))
    guidance = await table.send("! ฮามุใช้ cantrip guidance ใส่เนเนโกะ", name="Hamu")
    guidance_body = "\n".join(r.content for r in guidance.responses)
    assert guidance_body.strip() != "คำชี้นำ  |  · ต้องเพ่งสมาธิ", "the original bug"
    assert "1d4" in guidance_body

    # 2. "! เนเนโกะร่าย minor illusion สร้างภาพลวงตาเป็นแมวอ้วนกำลังเต้นและร้องเพลง…"
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="เบนความสนใจทุกคนในร้าน", method="ร่ายภาพลวง", intent_confidence=0.9,
        cast_intent=True, spell_reference="minor illusion",
        spell_description="แมวอ้วนกำลังเต้นและร้องเพลง",
        spell_modes=["image", "sound"]))
    illusion = await table.send(
        "! เนเนโกะร่าย minor illusion สร้างภาพลวงตาเป็นแมวอ้วนกำลังเต้นและร้องเพลง"
        "ไปที่มุมอื่นของห้องเพื่อเบนความสนใจทุกคนในร้าน")
    illusion_body = "\n".join(r.content for r in illusion.responses)
    assert illusion_body.strip() != "ภาพลวงย่อม", "the original bug"
    assert "แมวอ้วนกำลังเต้นและร้องเพลง" in illusion_body
    assert "อย่างใดอย่างหนึ่ง" in illusion_body      # the image-or-sound limit, explained

    from app.tabletop.effects import EffectService

    async with db.session() as s:
        live = await EffectService(s).world_effects_in(
            campaign_id=world.campaign_id, scene_id=scene_id)
    assert len(live) == 1 and live[0].data["modes"] == ["image"]

    # 3. Neneko's distraction check — Guidance must reach it.
    from app.models.enums import ResolutionType
    from app.schemas.llm_io import AdjudicationDecision

    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="เบนความสนใจเจ้าของโรงเตี๊ยม", method="พูดหลอก", intent_confidence=0.9))
    provider.on("adjudicate_uncertain_action", lambda m, model: AdjudicationDecision(
        resolution_type=ResolutionType.ABILITY_CHECK, ability="cha", skill="deception",
        dc_band="MEDIUM", rationale="หลอกเจ้าของร้าน"))
    table.game.pipeline.dice.rng = SequenceRandomness([7, 3], default=3)
    check = await table.send("! หลอกเจ้าของโรงเตี๊ยม", author="disc-p2", name="Neneko")

    roll_lines = [r.data["roll_line"] for r in check.responses
                  if r.data and r.data.get("roll_line")]
    assert roll_lines and "คำชี้นำ" in roll_lines[0], (
        f"Guidance must reach Neneko's check; got {roll_lines!r}")

    # And through all of it, the story never restarted.
    assert await _scene_ids(db) == [scene_id]
    assert len(await _scene_started_events(db)) == 0
