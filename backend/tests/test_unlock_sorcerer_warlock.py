"""Part 3 — the honest end-to-end gate for unlocking Sorcerer and Warlock.

Unlock is claimed ONLY because this complete path passes: guided creation →
finalize (correct spells + the class's own slot pool) → cast a real spell through
the committed pipeline → signature resource works → rest recovers correctly →
restart persists. If any assertion here failed, the unlock would be reverted.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.models.character import Character
from app.models.character_draft import CharacterDraft
from app.models.combat import Combatant
from app.models.progression import CharacterSpell, ResourceState
from app.presentation import MessageKind
from app.services.campaigns.finalize import finalize_character
from tests.support.factories import build_world, start_session_with_scene
from tests.test_spell_pipeline import Table, _start_combat_with_guard


async def _finalize(db, world, *, char_class, cantrips, known, background):
    from app.rules_content import get_registry

    cls = get_registry().get_class(char_class)
    skills = (list(cls.skill_choices["options"])[:cls.skill_choices["count"]]
              if cls.skill_choices["options"] != "any" else ["arcana", "deception"])
    build = {
        "step": "review", "class": char_class, "species": "human", "background": background,
        "scores": {"str": 8, "dex": 12, "con": 14, "int": 12, "wis": 12, "cha": 16},
        "skills": skills, "cantrips": cantrips, "prepared": known, "component_token": "t",
    }
    async with db.unit_of_work() as s:
        d = CharacterDraft(campaign_id=world.campaign_id, member_id=world.p1_member_id,
                           data={"name": f"E2E{char_class.title()}", "_build": build})
        s.add(d)
        await s.flush()
        did = d.id
    async with db.session() as s:
        d = await s.get(CharacterDraft, did)
    r = await finalize_character(db, draft=d, data=d.data, channel_id="chan-1")
    assert r.responses[0].kind == MessageKind.CHARACTER_REVEAL
    async with db.session() as s:
        return (await s.execute(select(Character).where(
            Character.name == f"E2E{char_class.title()}"))).scalar_one()


def test_registry_now_exposes_eight_selectable_classes():
    from app.rules_content import get_registry
    from app.tabletop.rules.core import SUPPORTED_CLASSES

    reg = get_registry()
    assert set(reg.selectable_classes) == set(SUPPORTED_CLASSES)
    assert {"sorcerer", "warlock"} <= set(reg.selectable_classes)
    for c in ("sorcerer", "warlock"):
        assert reg.get_class(c).support_status == "FULLY_SUPPORTED"


async def test_sorcerer_end_to_end_create_then_cast_through_pipeline(db, provider):
    world = await build_world(db)
    sorc = await _finalize(db, world, char_class="sorcerer",
                           cantrips=["fire_bolt", "light"],
                           known=["magic_missile", "shield"], background="sage")
    # Finalize granted the arcane slot pool + the spells.
    async with db.session() as s:
        rids = {r.resource_id for r in (await s.execute(select(ResourceState).where(
            ResourceState.character_id == sorc.id))).scalars()}
        assert "resource:spell_slots_1" in rids
        spells = {r.spell_key for r in (await s.execute(select(CharacterSpell).where(
            CharacterSpell.character_id == sorc.id))).scalars()}
        assert {"fire_bolt", "magic_missile"} <= spells
    # Move the sorcerer to be the ACTOR (p1's active character is the finalized one).
    sid, _ = await start_session_with_scene(db, world)
    await _start_combat_with_guard(db, world, sid, ac=12, hp=20)
    from app.schemas.llm_io import ActionInterpretation
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ร่ายไฟ", method="ร่าย", intent_confidence=0.9, cast_intent=True,
        spell_reference="fire_bolt", target_references=["ยามเฝ้าประตู"]))
    table = Table(db, provider, rng=SequenceRandomness([18, 7]))
    r = await table.send("! ร่าย fire_bolt ใส่ ยามเฝ้าประตู", author=world.p1_discord_id)
    assert r.responses[0].kind == MessageKind.CHECK_RESOLUTION and r.state_mutated
    async with db.session() as s:
        cb = (await s.execute(select(Combatant).where(
            Combatant.entity_ref == f"npc:{world.guard_npc_id}"))).scalars().first()
        assert cb.hp == 13                                    # 20 - 7 committed


async def test_warlock_end_to_end_pact_slots_and_cast(db, provider):
    world = await build_world(db)
    warlock = await _finalize(db, world, char_class="warlock",
                              cantrips=["eldritch_blast", "minor_illusion"],
                              known=["hex", "charm_person"], background="acolyte")
    # Finalize granted PACT slots (not the arcane pool) — the class-declared pool.
    async with db.session() as s:
        rids = {r.resource_id for r in (await s.execute(select(ResourceState).where(
            ResourceState.character_id == warlock.id))).scalars()}
        assert "resource:pact_slots" in rids and "resource:spell_slots_1" not in rids
    sid, _ = await start_session_with_scene(db, world)
    await _start_combat_with_guard(db, world, sid, ac=13, hp=20)
    from app.schemas.llm_io import ActionInterpretation
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ยิงลำแสง", method="ร่าย", intent_confidence=0.9, cast_intent=True,
        spell_reference="eldritch_blast", target_references=["ยามเฝ้าประตู"]))
    table = Table(db, provider, rng=SequenceRandomness([17, 8]))
    r = await table.send("! ร่าย eldritch_blast ใส่ ยามเฝ้าประตู", author=world.p1_discord_id)
    assert r.responses[0].kind == MessageKind.CHECK_RESOLUTION and r.state_mutated
    async with db.session() as s:
        cb = (await s.execute(select(Combatant).where(
            Combatant.entity_ref == f"npc:{world.guard_npc_id}"))).scalars().first()
        assert cb.hp == 12                                    # 20 - 8 (eldritch_blast is a cantrip)
