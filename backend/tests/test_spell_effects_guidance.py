"""Guidance is a real mechanical effect, not a line of prose.

The reported bug, exactly: Hamu casts Guidance on Neneko; Reverie acknowledges it;
Neneko later tries to deceive the innkeeper; the roll happens with only her normal
modifier and Guidance is never mentioned, applied, or consumed. The cast produced
narration and nothing else.

These tests drive the PRODUCTION bridge (not the engine directly), because the bug
lived in the wiring between casting and rolling — an engine-only test would have
passed while the table stayed broken.

Guidance is the CASE here, not the SUBJECT: every assertion below rests on the
spell's declared `effects` metadata, so the same machinery carries Bless and any
future buff. Nothing in the engine names Guidance.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import AdminBridge, InboundMessage, is_admin_command
from app.engine import build_bridge
from app.models.campaign import Campaign
from app.models.character import Character
from app.models.progression import ActiveEffect, CharacterSpell
from app.schemas.llm_io import ActionInterpretation, AdjudicationDecision
from app.models.enums import ResolutionType
from app.tabletop.effects import EffectService
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1", name="Hamu", mid=None):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=mid or f"gd{_n['v']}", guild_id="guild-1",
        channel_id="chan-1", author_discord_id=author,
        author_display_name=name, content=content,
    )


class Table:
    def __init__(self, db, provider, rng=None):
        # default=3 is a legal face for a d4 as well as a d20 — a bonus die is rolled
        # on these paths, and SequenceRandomness rejects an out-of-range face.
        self.game = build_bridge(db, provider=provider,
                                 rng=rng or SequenceRandomness(default=3))
        self.admin = AdminBridge(db, provider, creation_flow=self.game.creation_flow,
                                 session_zero=self.game.session_zero)

    async def send(self, content, author="disc-p1", name="Hamu", mid=None):
        inbound = _msg(content, author=author, name=name, mid=mid)
        if is_admin_command(content):
            return await self.admin.handle(inbound)
        return await self.game.handle_inbound(inbound)


async def _make_cleric(db, world, cantrips=("guidance",)):
    """Hamu (kael) is the caster; Neneko (bront) is the one who gets the die."""
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        c.char_class = "cleric"
        c.wis_score = 16
        for cn in cantrips:
            s.add(CharacterSpell(character_id=c.id, spell_key=cn, kind="cantrip"))


def _cast_guidance_on(provider, target_name: str):
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ช่วยเพื่อน", method="ร่ายคาถา", intent_confidence=0.9,
        cast_intent=True, spell_reference="guidance",
        target_references=[target_name]))


def _neneko_deceives(provider):
    """Neneko's follow-up: an ability check that Guidance is eligible for."""
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="เบนความสนใจเจ้าของโรงเตี๊ยม", method="พูดหลอก",
        intent_confidence=0.9, target_references=[]))
    provider.on("adjudicate_uncertain_action", lambda m, model: AdjudicationDecision(
        resolution_type=ResolutionType.ABILITY_CHECK, ability="cha",
        skill="deception", dc_band="MEDIUM", rationale="หลอกเจ้าของร้าน"))


async def _guidance_rows(db, subject_ref, *, active=True):
    async with db.session() as s:
        return (await s.execute(select(ActiveEffect).where(
            ActiveEffect.spell_key == "guidance",
            ActiveEffect.kind == "roll_bonus",
            ActiveEffect.subject_ref == subject_ref,
            ActiveEffect.active.is_(active)))).scalars().all()


# --- 1-2: the effect is stored on the TARGET, not the caster -------------------

async def test_guidance_is_stored_on_the_target_character(db, provider):
    world = await build_world(db)
    await _make_cleric(db, world)
    await start_session_with_scene(db, world)
    _cast_guidance_on(provider, "Bront")
    table = Table(db, provider)

    await table.send("! ร่าย guidance ใส่ Bront")

    rows = await _guidance_rows(db, f"character:{world.bront_id}")
    assert len(rows) == 1, "Guidance must attach to the character it was cast on"
    effect = rows[0]
    # The maintainer is the CASTER (he holds concentration); the SUBJECT is the ally.
    # Conflating the two is the original bug: the target was dropped entirely.
    assert effect.character_id == world.kael_id
    assert effect.subject_ref == f"character:{world.bront_id}"
    assert effect.data["dice"] == "1d4"


async def test_guidance_effect_is_not_stored_on_the_caster(db, provider):
    world = await build_world(db)
    await _make_cleric(db, world)
    await start_session_with_scene(db, world)
    _cast_guidance_on(provider, "Bront")
    table = Table(db, provider)

    await table.send("! ร่าย guidance ใส่ Bront")

    assert await _guidance_rows(db, f"character:{world.kael_id}") == [], (
        "casting on an ally must not buff the caster")


# --- 3-5: the roll shows it, and the total includes it -------------------------

async def test_roll_prompt_shows_guidance_and_total_includes_the_die(db, provider):
    """The dice-ritual prompt from the screenshot ('โมดิฟายเออร์ +2' with a ทอย d20
    button) must now also announce the 1d4 — and the resolved total must contain it."""
    world = await build_world(db)
    await _make_cleric(db, world)
    sid, _ = await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        campaign = await s.get(Campaign, world.campaign_id)
        campaign.config = {**(campaign.config or {}), "dice_mode": "PLAYER_CLICK"}

    _cast_guidance_on(provider, "Bront")
    table = Table(db, provider)
    await table.send("! ร่าย guidance ใส่ Bront")

    # Neneko (p2/bront) now acts. d20=7, guidance d4=3.
    _neneko_deceives(provider)
    table.game.pipeline.dice.rng = SequenceRandomness([7, 3], default=3)
    prompt = await table.send("! หลอกเจ้าของโรงเตี๊ยม", author="disc-p2", name="Neneko")
    prompt_text = "\n".join(r.content for r in prompt.responses)
    assert "คำชี้นำ: +1d4" in prompt_text, (
        f"the roll prompt must show the available help; got: {prompt_text!r}")

    resolved = await table.send("ทอย", author="disc-p2", name="Neneko")
    data = [r.data for r in resolved.responses if r.data and r.data.get("roll_line")]
    assert data, "expected a resolved roll line"
    roll_line = data[0]["roll_line"]
    assert "คำชี้นำ" in roll_line, f"the die must be visible in the maths: {roll_line!r}"
    assert "1d4(3)" in roll_line, f"the rolled die must be shown: {roll_line!r}"


async def test_resolved_total_arithmetic_includes_the_bonus_die(db, provider):
    world = await build_world(db)
    await _make_cleric(db, world)
    await start_session_with_scene(db, world)
    _cast_guidance_on(provider, "Bront")
    table = Table(db, provider)
    await table.send("! ร่าย guidance ใส่ Bront")

    _neneko_deceives(provider)
    table.game.pipeline.dice.rng = SequenceRandomness([7, 3], default=3)
    result = await table.send("! หลอกเจ้าของโรงเตี๊ยม", author="disc-p2", name="Neneko")

    async with db.session() as s:
        from app.models.event import Event

        rolls = (await s.execute(select(Event).where(
            Event.event_type == "ABILITY_CHECK_RESOLVED"))).scalars().all()
    assert rolls, "the check must have been recorded"
    mech = rolls[-1].mechanical_changes
    bonus = mech["bonus_dice"]
    assert len(bonus) == 1 and bonus[0]["total"] == 3
    assert mech["total"] == mech["natural_roll"] + mech["modifier"] + 3
    assert result.handled


# --- 6: consumed after use -----------------------------------------------------

async def test_guidance_is_consumed_by_the_roll_it_helps(db, provider):
    world = await build_world(db)
    await _make_cleric(db, world)
    await start_session_with_scene(db, world)
    _cast_guidance_on(provider, "Bront")
    table = Table(db, provider)
    await table.send("! ร่าย guidance ใส่ Bront")
    assert len(await _guidance_rows(db, f"character:{world.bront_id}")) == 1

    _neneko_deceives(provider)
    await table.send("! หลอกเจ้าของโรงเตี๊ยม", author="disc-p2", name="Neneko")

    assert await _guidance_rows(db, f"character:{world.bront_id}") == [], (
        "Guidance is spent by the first eligible check")

    # A second check gets no die — the effect is gone, not merely hidden.
    async with db.session() as s:
        grants = await EffectService(s).bonus_grants_for(
            campaign_id=world.campaign_id,
            subject_ref=f"character:{world.bront_id}",
            roll_type="ability_check", ability="cha")
    assert grants == []


# --- 7: ineligible rolls --------------------------------------------------------

async def test_guidance_does_not_apply_to_an_ineligible_roll(db, provider):
    """Guidance declares ability_check only. An attack roll must not see it — and
    this is decided by the declaration, not by an if-statement about Guidance."""
    world = await build_world(db)
    await _make_cleric(db, world)
    await start_session_with_scene(db, world)
    _cast_guidance_on(provider, "Bront")
    table = Table(db, provider)
    await table.send("! ร่าย guidance ใส่ Bront")

    async with db.session() as s:
        service = EffectService(s)
        assert await service.bonus_grants_for(
            campaign_id=world.campaign_id, subject_ref=f"character:{world.bront_id}",
            roll_type="attack_roll", ability="str") == []
        assert await service.bonus_grants_for(
            campaign_id=world.campaign_id, subject_ref=f"character:{world.bront_id}",
            roll_type="saving_throw", ability="dex") == []
        assert await service.bonus_grants_for(
            campaign_id=world.campaign_id, subject_ref=f"character:{world.bront_id}",
            roll_type="ability_check", ability="cha")


async def test_a_bystander_gets_no_die(db, provider):
    world = await build_world(db)
    await _make_cleric(db, world)
    await start_session_with_scene(db, world)
    _cast_guidance_on(provider, "Bront")
    table = Table(db, provider)
    await table.send("! ร่าย guidance ใส่ Bront")

    async with db.session() as s:
        assert await EffectService(s).bonus_grants_for(
            campaign_id=world.campaign_id, subject_ref=f"character:{world.kael_id}",
            roll_type="ability_check", ability="cha") == []


# --- 8: expiry ------------------------------------------------------------------

async def test_expired_guidance_is_not_applied(db, provider):
    """Guidance lasts 1 minute. Once the clock passes it the die is gone, whether or
    not a sweep has run — the read path filters on the clock."""
    world = await build_world(db)
    await _make_cleric(db, world)
    await start_session_with_scene(db, world)
    _cast_guidance_on(provider, "Bront")
    table = Table(db, provider)
    await table.send("! ร่าย guidance ใส่ Bront")

    subject = f"character:{world.bront_id}"
    async with db.session() as s:
        assert await EffectService(s).bonus_grants_for(
            campaign_id=world.campaign_id, subject_ref=subject,
            roll_type="ability_check", ability="cha", game_time=0)
        # One in-world minute later it has lapsed.
        assert await EffectService(s).bonus_grants_for(
            campaign_id=world.campaign_id, subject_ref=subject,
            roll_type="ability_check", ability="cha", game_time=1) == []

    async with db.unit_of_work() as s:
        ended = await EffectService(s).expire_due(
            campaign_id=world.campaign_id, game_time=5)
    assert [e.spell_key for e in ended] == ["guidance"]
    assert await _guidance_rows(db, subject) == []


# --- 9: concentration -----------------------------------------------------------

async def test_a_second_concentration_spell_ends_guidance(db, provider):
    """Guidance needs concentration. Casting another concentration spell drops it —
    and the die it was granting must go with it."""
    world = await build_world(db)
    await _make_cleric(db, world, cantrips=("guidance", "dancing_lights"))
    await start_session_with_scene(db, world)
    _cast_guidance_on(provider, "Bront")
    table = Table(db, provider)
    await table.send("! ร่าย guidance ใส่ Bront")
    assert len(await _guidance_rows(db, f"character:{world.bront_id}")) == 1

    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="จุดไฟ", method="ร่ายคาถา", intent_confidence=0.9, cast_intent=True,
        spell_reference="dancing_lights", target_references=[]))
    await table.send("! ร่าย dancing_lights")

    assert await _guidance_rows(db, f"character:{world.bront_id}") == [], (
        "losing concentration must take the granted die with it")
    async with db.session() as s:
        assert await EffectService(s).bonus_grants_for(
            campaign_id=world.campaign_id, subject_ref=f"character:{world.bront_id}",
            roll_type="ability_check", ability="cha") == []


# --- 10: survives a restart ------------------------------------------------------

async def test_guidance_survives_a_worker_restart(tmp_path, provider):
    from app.db.session import Database

    url = f"sqlite+aiosqlite:///{(tmp_path / 'guidance-restart.db').as_posix()}"
    first = Database(url, echo=False)
    await first.create_all()
    try:
        world = await build_world(first)
        await _make_cleric(first, world)
        await start_session_with_scene(first, world)
        _cast_guidance_on(provider, "Bront")
        await Table(first, provider).send("! ร่าย guidance ใส่ Bront")
    finally:
        await first.dispose()

    restarted = Database(url, echo=False)
    try:
        async with restarted.session() as s:
            grants = await EffectService(s).bonus_grants_for(
                campaign_id=world.campaign_id,
                subject_ref=f"character:{world.bront_id}",
                roll_type="ability_check", ability="cha")
        assert [g.expression for g in grants] == ["1d4"], (
            "a persisted effect must come back usable after a restart")
    finally:
        await restarted.dispose()


# --- the pipeline is general, not Guidance-shaped --------------------------------

async def test_the_cast_response_is_never_a_bare_spell_name(db, provider):
    """The original symptom: the entire reply was the spell's own name. Any spell
    that creates an effect must say what it created."""
    world = await build_world(db)
    await _make_cleric(db, world)
    await start_session_with_scene(db, world)
    _cast_guidance_on(provider, "Bront")
    table = Table(db, provider)

    result = await table.send("! ร่าย guidance ใส่ Bront")

    body = "\n".join(r.content for r in result.responses)
    assert body.strip() not in ("คำชี้นำ", "คำชี้นำ  |  · ต้องเพ่งสมาธิ")
    assert "1d4" in body, f"the cast must state what it granted; got {body!r}"
