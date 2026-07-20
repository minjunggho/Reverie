"""Shared decision-window engine core (the new unit of resolution).

Server-authoritative state machine: readiness/freeze/resolution are computed from
persisted rows, never chat order. Covers the spec's numbered scenarios that the engine
core owns (transport/UI-only ones — manual-roll UI, reconnect socket, NPC turns — are
the next phase and are noted in docs/multiplayer-rounds.md).
"""
from __future__ import annotations

import pytest

from app.core.errors import ConflictError, RulesViolation
from app.core.randomness import SequenceRandomness
from app.models.combat import Combatant, CombatEncounter
from app.models.enums import (
    SubmissionValidation,
    SubmissionVisibility,
    WindowMode,
    WindowPhase,
)
from app.models.scene import Scene
from app.rounds import DecisionWindowService, RoundResolver, WindowPolicies
from app.rounds.validation import validate_submission
from app.entities import SceneEntityDirectory
from app.services.scenes import SceneService
from tests.support.factories import build_world, start_session_with_scene


# --- helpers -----------------------------------------------------------------

async def _open(db, world, sid, scene_id, required, *, mode=WindowMode.NONCOMBAT,
                policies=None):
    async with db.unit_of_work() as s:
        w = await DecisionWindowService(s).open_window(
            campaign_id=world.campaign_id, session_id=sid, scene_id=scene_id,
            round_id=1, mode=mode, required_actor_ids=required, policies=policies)
        return w.id


async def _submit(db, wid, actor, *, raw="", fields=None, visibility=None,
                  idem=None, expected_revision=None):
    async with db.unit_of_work() as s:
        sub = await DecisionWindowService(s).submit(
            window_id=wid, actor_id=actor, raw_text=raw, fields=fields,
            visibility=visibility, idempotency_key=idem, expected_revision=expected_revision)
        return sub.revision, sub.is_ready


async def _ready(db, wid, actor, revision):
    async with db.unit_of_work() as s:
        await DecisionWindowService(s).mark_ready(
            window_id=wid, actor_id=actor, revision=revision)


async def _all_ready(db, wid):
    async with db.session() as s:
        svc = DecisionWindowService(s)
        return await svc.all_required_ready(await svc.get(wid))


async def _phase(db, wid):
    async with db.session() as s:
        return (await DecisionWindowService(s).get(wid)).phase


# --- 1: submit + edit before ready -------------------------------------------

async def test_two_players_submit_and_edit_before_ready(db, provider):
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id])

    rev_k, _ = await _submit(db, wid, world.kael_id, raw="ผมย่องไปที่หน้าต่าง")
    rev_b, _ = await _submit(db, wid, world.bront_id, raw="ผมยืนเฝ้าประตู")
    assert rev_k == 1 and rev_b == 1
    # Kael revises before anyone is ready.
    rev_k2, _ = await _submit(db, wid, world.kael_id, raw="เปลี่ยนใจ ผมปีนขึ้นหลังคาแทน")
    assert rev_k2 == 2
    assert not await _all_ready(db, wid)


# --- 2: editing unsets ready --------------------------------------------------

async def test_editing_automatically_unsets_ready(db, provider):
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    # Two required actors, so solo auto-ready does not apply and Ready is manual.
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id])

    rev, _ = await _submit(db, wid, world.kael_id, raw="ผมงัดหีบ")
    await _ready(db, wid, world.kael_id, rev)
    async with db.session() as s:
        sub = await DecisionWindowService(s)._submission(wid, world.kael_id)
        assert sub.is_ready
    # Editing clears Ready and bumps the revision.
    rev2, ready = await _submit(db, wid, world.kael_id, raw="ผมงัดหีบเบาๆ ไม่ให้มีเสียง")
    assert rev2 == 2 and ready is False
    async with db.session() as s:
        sub = await DecisionWindowService(s)._submission(wid, world.kael_id)
        assert not sub.is_ready


# --- 3: resolution gated until all required ready ----------------------------

async def test_freeze_is_gated_until_all_required_ready(db, provider):
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id])

    rk, _ = await _submit(db, wid, world.kael_id, raw="A")
    rb, _ = await _submit(db, wid, world.bront_id, raw="B")
    await _ready(db, wid, world.kael_id, rk)
    assert not await _all_ready(db, wid)
    # Freezing before everyone is ready is refused.
    with pytest.raises(RulesViolation):
        async with db.unit_of_work() as s:
            svc = DecisionWindowService(s)
            await svc.freeze(await svc.get(wid))
    await _ready(db, wid, world.bront_id, rb)
    assert await _all_ready(db, wid)
    async with db.unit_of_work() as s:
        svc = DecisionWindowService(s)
        snap = await svc.freeze(await svc.get(wid))
    assert len(snap["submissions"]) == 2
    assert await _phase(db, wid) == WindowPhase.READY_TO_RESOLVE.value


# --- 4: host force-resolve ----------------------------------------------------

async def test_host_force_resolve_freezes_without_all_ready(db, provider):
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id])
    await _submit(db, wid, world.kael_id, raw="A")
    # bront never readies; host forces it.
    async with db.unit_of_work() as s:
        svc = DecisionWindowService(s)
        snap = await svc.force_resolve(await svc.get(wid))
    assert snap["forced"] is True
    assert await _phase(db, wid) == WindowPhase.READY_TO_RESOLVE.value


# --- 5: one invalid submission does not discard the others -------------------

async def test_one_invalid_submission_does_not_discard_others(db, provider):
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id])

    rk, _ = await _submit(db, wid, world.kael_id, raw="ผมคุยกับยาม",
                          fields={"action_target": "ยามเฝ้าประตู"})
    # bront targets someone not in the scene -> NEEDS_CORRECTION.
    await _submit(db, wid, world.bront_id, raw="ผมโจมตีมังกร",
                  fields={"action_target": "มังกรทองคำ"})
    async with db.unit_of_work() as s:
        svc = DecisionWindowService(s)
        window = await svc.get(wid)
        scene = await SceneService(s).get_active_scene(sid)
        directory = await SceneEntityDirectory(s).build(
            scene, actor_character_id=None, campaign_id=world.campaign_id)
        k = await svc._submission(wid, world.kael_id)
        b = await svc._submission(wid, world.bront_id)
        rk_res = await validate_submission(s, window=window, submission=k, scene=scene,
                                           directory=directory, campaign_id=world.campaign_id)
        rb_res = await validate_submission(s, window=window, submission=b, scene=scene,
                                           directory=directory, campaign_id=world.campaign_id)
    assert rk_res.ok
    assert rb_res.status == SubmissionValidation.NEEDS_CORRECTION.value
    # Kael's valid submission survived; only bront must correct.
    await _ready(db, wid, world.kael_id, rk)
    assert not await _all_ready(db, wid)
    # bront corrects to a present target and readies.
    rb2, _ = await _submit(db, wid, world.bront_id, raw="ผมโจมตียาม",
                           fields={"action_target": "ยามเฝ้าประตู"})
    await _ready(db, wid, world.bront_id, rb2)
    assert await _all_ready(db, wid)


# --- 6 / 7: cooperative + conflicting classification --------------------------

async def test_two_cooperative_actions_are_classified(db, provider):
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id])
    rk, _ = await _submit(db, wid, world.kael_id, raw="ผมยกประตูบานหนักไว้ให้")
    rb, _ = await _submit(db, wid, world.bront_id, raw="ผมคลานลอดใต้ประตูไป")
    await _ready(db, wid, world.kael_id, rk)
    await _ready(db, wid, world.bront_id, rb)
    pkg = await RoundResolver(db, provider, SequenceRandomness(default=6)).resolve(window_id=wid)
    assert pkg.per_actor_relationship[world.kael_id] == "COOPERATIVE"
    assert pkg.per_actor_relationship[world.bront_id] == "COOPERATIVE"


async def test_two_conflicting_actions_are_classified(db, provider):
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id])
    rk, _ = await _submit(db, wid, world.kael_id, raw="ผมปล่อยนักโทษ",
                          fields={"action_target": "นักโทษ"})
    rb, _ = await _submit(db, wid, world.bront_id, raw="ผมฆ่านักโทษ",
                          fields={"action_target": "นักโทษ"})
    await _ready(db, wid, world.kael_id, rk)
    await _ready(db, wid, world.bront_id, rb)
    pkg = await RoundResolver(db, provider, SequenceRandomness(default=6)).resolve(window_id=wid)
    assert pkg.per_actor_relationship[world.kael_id] == "CONFLICTING"
    assert any(p["relationship"] == "CONFLICTING" for p in pkg.relationships)


# --- combat: initiative order + kill-invalidates + fallback ------------------

async def _combat(db, world, sid, scene_id, *, kael_init, bront_init, guard_hp,
                  second_enemy=False):
    async with db.unit_of_work() as s:
        enc = CombatEncounter(campaign_id=world.campaign_id, session_id=sid,
                              scene_id=scene_id, status="active")
        s.add(enc)
        await s.flush()

        def cb(ref, name, init, *, hp, ac=5, atk=10, die=12, dmg=5, pc=False):
            return Combatant(encounter_id=enc.id, entity_ref=ref, name=name,
                             initiative=init, hp=hp, max_hp=hp, ac=ac, attack_bonus=atk,
                             damage_die=die, damage_bonus=dmg, is_pc=pc, alive=True)
        s.add(cb(f"character:{world.kael_id}", "Kael", kael_init, hp=30, die=12, dmg=5, pc=True))
        s.add(cb(f"character:{world.bront_id}", "Bront", bront_init, hp=30, die=6, dmg=2, pc=True))
        s.add(cb(f"npc:{world.guard_npc_id}", "ยามเฝ้าประตู", 1, hp=guard_hp))
        if second_enemy:
            from app.npcs.npc_service import NPCService
            npc2 = await NPCService(s).create_npc(
                campaign_id=world.campaign_id, name="ผู้คุมคนที่สอง",
                personality="ดุ", current_location_id=world.location_id)
            s.add(cb(f"npc:{npc2.id}", "ผู้คุมคนที่สอง", 1, hp=20))
            scene = await s.get(Scene, scene_id)
            scene.visible_entity_ids = list(scene.visible_entity_ids or []) + [f"npc:{npc2.id}"]


async def test_initiative_orders_resolution_and_an_earlier_kill_invalidates_later(db, provider):
    """Spec 8 + 9: the higher-initiative attacker resolves first and kills the guard;
    the lower-initiative attacker's action against the same guard is invalidated."""
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    await _combat(db, world, sid, scene_id, kael_init=20, bront_init=3, guard_hp=3)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id],
                      mode=WindowMode.COMBAT)
    rk, _ = await _submit(db, wid, world.kael_id, raw="ผมฟันยาม",
                          fields={"action_target": "ยามเฝ้าประตู"})
    rb, _ = await _submit(db, wid, world.bront_id, raw="ผมฟันยามซ้ำ",
                          fields={"action_target": "ยามเฝ้าประตู"})
    await _ready(db, wid, world.kael_id, rk)
    await _ready(db, wid, world.bront_id, rb)

    pkg = await RoundResolver(db, provider, SequenceRandomness(default=6)).resolve(window_id=wid)
    # Kael (init 20) is ordered first.
    assert pkg.initiative[0] == f"character:{world.kael_id}"
    statuses = {a["actor_id"]: a["status"] for a in pkg.resolved_actions}
    assert statuses[world.kael_id] == "resolved"
    assert statuses[world.bront_id] == "invalidated"     # guard already down
    assert any(i["actor_id"] == world.bront_id for i in pkg.invalidated)


async def test_declared_fallback_used_when_primary_target_is_down(db, provider):
    """Spec 10: bront's primary target dies to kael's strike; his declared fallback
    target (a second, living enemy) is used instead — never a silently invented action."""
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    await _combat(db, world, sid, scene_id, kael_init=20, bront_init=3, guard_hp=3,
                  second_enemy=True)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id],
                      mode=WindowMode.COMBAT)
    rk, _ = await _submit(db, wid, world.kael_id, raw="ผมฟันยาม",
                          fields={"action_target": "ยามเฝ้าประตู"})
    rb, _ = await _submit(
        db, wid, world.bront_id, raw="ถ้ายามตายแล้ว ผมฟันผู้คุมคนที่สองแทน",
        fields={"action_target": "ยามเฝ้าประตู", "fallback_target": "ผู้คุมคนที่สอง"})
    await _ready(db, wid, world.kael_id, rk)
    await _ready(db, wid, world.bront_id, rb)

    pkg = await RoundResolver(db, provider, SequenceRandomness(default=6)).resolve(window_id=wid)
    statuses = {a["actor_id"]: a["status"] for a in pkg.resolved_actions}
    assert statuses[world.bront_id] == "fallback"
    assert any(f["actor_id"] == world.bront_id for f in pkg.fallbacks)


async def test_automatic_attack_rolls_are_recorded_in_the_package(db, provider):
    """Spec 13: the deterministic engine rolls and every die/total/hit is captured."""
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    await _combat(db, world, sid, scene_id, kael_init=20, bront_init=10, guard_hp=40)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id],
                      mode=WindowMode.COMBAT)
    rk, _ = await _submit(db, wid, world.kael_id, raw="ฟันยาม",
                          fields={"action_target": "ยามเฝ้าประตู"})
    rb, _ = await _submit(db, wid, world.bront_id, raw="ฟันยาม",
                          fields={"action_target": "ยามเฝ้าประตู"})
    await _ready(db, wid, world.kael_id, rk)
    await _ready(db, wid, world.bront_id, rb)
    pkg = await RoundResolver(db, provider, SequenceRandomness(default=6)).resolve(window_id=wid)
    assert len(pkg.roll_results) == 2
    assert all("natural_roll" in r and "attack_total" in r for r in pkg.roll_results)
    assert pkg.damage      # damage applied and recorded


# --- concurrency / idempotency -----------------------------------------------

async def test_duplicate_ready_requests_are_idempotent(db, provider):
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id])
    rk, _ = await _submit(db, wid, world.kael_id, raw="A")
    await _ready(db, wid, world.kael_id, rk)
    first = None
    async with db.session() as s:
        first = (await DecisionWindowService(s)._submission(wid, world.kael_id)).ready_at
    await _ready(db, wid, world.kael_id, rk)     # duplicate
    async with db.session() as s:
        subs = await DecisionWindowService(s).submissions(wid)
    assert len([x for x in subs if x.actor_id == world.kael_id]) == 1   # no duplicate row
    async with db.session() as s:
        assert (await DecisionWindowService(s)._submission(wid, world.kael_id)).ready_at == first


async def test_duplicate_resolution_returns_the_same_frozen_package(db, provider):
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id])
    await _submit(db, wid, world.kael_id, raw="ผมมองไปรอบๆ")     # solo auto-ready
    resolver = RoundResolver(db, provider, SequenceRandomness(default=6))
    pkg1 = await resolver.resolve(window_id=wid)
    pkg2 = await resolver.resolve(window_id=wid)       # re-resolve
    assert pkg1.window_id == pkg2.window_id
    assert pkg2.resolved_actions == pkg1.resolved_actions
    async with db.session() as s:
        assert (await DecisionWindowService(s).get(wid)).resolved is True


async def test_two_clients_editing_the_same_revision_conflicts(db, provider):
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id])
    await _submit(db, wid, world.kael_id, raw="เวอร์ชันแรก")   # revision 1
    # Client A edits (revision -> 2).
    await _submit(db, wid, world.kael_id, raw="เวอร์ชัน A", expected_revision=1)
    # Client B still thinks it is on revision 1 -> rejected, not merged.
    with pytest.raises(ConflictError):
        await _submit(db, wid, world.kael_id, raw="เวอร์ชัน B", expected_revision=1)


async def test_idempotent_submit_with_same_key_is_a_noop(db, provider):
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id])
    r1, _ = await _submit(db, wid, world.kael_id, raw="ครั้งเดียว", idem="msg-1")
    r2, _ = await _submit(db, wid, world.kael_id, raw="ครั้งเดียว", idem="msg-1")
    assert r1 == r2 == 1        # the retry did not bump the revision


# --- single-player fast path --------------------------------------------------

async def test_single_player_auto_ready_and_resolves(db, provider):
    """Spec 22: a solo eligible player readies on submit and the round resolves at once."""
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id])
    _, ready = await _submit(db, wid, world.kael_id, raw="ผมเปิดประตู")
    assert ready is True                       # auto-ready, no click
    assert await _all_ready(db, wid)
    pkg = await RoundResolver(db, provider, SequenceRandomness(default=6)).resolve(window_id=wid)
    assert pkg.resolved_actions and pkg.resolved_actions[0]["actor_id"] == world.kael_id


async def test_manual_ready_solo_policy_disables_auto_ready(db, provider):
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id],
                      policies=WindowPolicies(manual_ready_solo=True))
    _, ready = await _submit(db, wid, world.kael_id, raw="ผมเปิดประตู")
    assert ready is False
    assert not await _all_ready(db, wid)


# --- secret actions -----------------------------------------------------------

async def test_secret_action_is_hidden_from_other_players_in_the_panel(db, provider):
    """Spec 23: a secret submission's contents are hidden from others; its existence and
    readiness are still visible so the table knows the actor has acted."""
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id])
    await _submit(db, wid, world.kael_id, raw="ผมแอบหยิบกุญแจจากเข็มขัดยาม",
                  visibility=SubmissionVisibility.SECRET.value)
    async with db.session() as s:
        svc = DecisionWindowService(s)
        window = await svc.get(wid)
        as_owner = await svc.panel(window, viewer_id=world.kael_id)
        as_other = await svc.panel(window, viewer_id=world.bront_id)
    owner_card = next(c for c in as_owner["cards"] if c["actor_id"] == world.kael_id)
    other_card = next(c for c in as_other["cards"] if c["actor_id"] == world.kael_id)
    assert owner_card["preview"] and "กุญแจ" in owner_card["preview"]
    assert other_card["secret"] is True
    assert other_card["preview"] is None            # contents hidden from bront


# --- disconnect / host reopen -------------------------------------------------

async def test_disconnected_player_can_be_excused_so_the_round_resolves(db, provider):
    """Spec 15: an excused (disconnected/AFK) actor no longer blocks the ready gate."""
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id])
    rk, _ = await _submit(db, wid, world.kael_id, raw="A")
    await _ready(db, wid, world.kael_id, rk)
    assert not await _all_ready(db, wid)      # bront still required
    async with db.unit_of_work() as s:
        svc = DecisionWindowService(s)
        await svc.excuse_actor(window_id=wid, actor_id=world.bront_id)
    assert await _all_ready(db, wid)          # excused -> no longer blocks


async def test_host_reopen_returns_to_planning_and_clears_ready(db, provider):
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id])
    rk, _ = await _submit(db, wid, world.kael_id, raw="A")
    await _ready(db, wid, world.kael_id, rk)
    async with db.unit_of_work() as s:
        svc = DecisionWindowService(s)
        await svc.force_resolve(await svc.get(wid))
    async with db.unit_of_work() as s:
        svc = DecisionWindowService(s)
        await svc.reopen(await svc.get(wid))
    assert await _phase(db, wid) == WindowPhase.AWAITING_ACTIONS.value
    async with db.session() as s:
        sub = await DecisionWindowService(s)._submission(wid, world.kael_id)
        assert not sub.is_ready                 # reopen cleared readiness


# --- combined narration -------------------------------------------------------

async def test_combined_narration_uses_several_players_words(db, provider):
    """Spec 25: one connected scene built from the players' own submitted wording."""
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    wid = await _open(db, world, sid, scene_id, [world.kael_id, world.bront_id])

    captured = {}

    def _round(messages, model):
        captured["blob"] = "\n".join(m.get("content", "") for m in messages)
        from app.schemas.llm_io import Narration
        return Narration(text="Kael เบี่ยงเบนความสนใจ ขณะ Bront ฉวยจังหวะคว้ากุญแจ",
                         decision_prompt="ประตูยังล็อกอยู่ — จะทำอะไรต่อ?")

    provider.on("generate_dm_narration", _round)
    await _submit(db, wid, world.kael_id, raw="ผมเบี่ยงเบนความสนใจยาม",
                  fields={"action_target": "ยามเฝ้าประตู"})
    rb, _ = await _submit(db, wid, world.bront_id, raw="ผมฉวยจังหวะคว้ากุญแจ")
    async with db.session() as s:
        rk = (await DecisionWindowService(s)._submission(wid, world.kael_id)).revision
    await _ready(db, wid, world.kael_id, rk)
    await _ready(db, wid, world.bront_id, rb)

    pkg = await RoundResolver(db, provider, SequenceRandomness(default=6)).resolve(window_id=wid)
    # The round package was handed to ONE narration call carrying both players' words.
    assert "ROUND_PACKAGE" in captured["blob"]
    assert "เบี่ยงเบนความสนใจยาม" in captured["blob"]
    assert "ฉวยจังหวะคว้ากุญแจ" in captured["blob"]
    assert pkg.narration and "Bront" in pkg.narration


# --- restart recovery ---------------------------------------------------------

async def test_restart_during_planning_persists_submissions(db, provider, tmp_path):
    """Spec 20: submissions survive a process restart (they are DB rows, not memory)."""
    from app.db.session import Database

    url = f"sqlite+aiosqlite:///{tmp_path}/plan.db"
    db1 = Database(url, echo=False)
    await db1.create_all()
    world = await build_world(db1)
    sid, scene_id = await start_session_with_scene(db1, world)
    wid = await _open(db1, world, sid, scene_id, [world.kael_id, world.bront_id])
    rk, _ = await _submit(db1, wid, world.kael_id, raw="แผนที่ยังพิมพ์ไม่เสร็จ")
    await _ready(db1, wid, world.kael_id, rk)
    await db1.dispose()

    # "Restart": a fresh Database on the same file, no create_all.
    db2 = Database(url, echo=False)
    async with db2.session() as s:
        svc = DecisionWindowService(s)
        window = await svc.get(wid)
        subs = await svc.submissions(wid)
        assert window.phase == WindowPhase.AWAITING_ACTIONS.value
        assert len(subs) == 1 and subs[0].is_ready
        assert subs[0].raw_player_text == "แผนที่ยังพิมพ์ไม่เสร็จ"
    await db2.dispose()


async def test_restart_during_resolution_does_not_double_apply(db, provider, tmp_path):
    """Spec 21: a resolved round is idempotent across a restart — re-resolving returns
    the stored package rather than applying the world update again."""
    from app.db.session import Database

    url = f"sqlite+aiosqlite:///{tmp_path}/resolve.db"
    db1 = Database(url, echo=False)
    await db1.create_all()
    world = await build_world(db1)
    sid, scene_id = await start_session_with_scene(db1, world)
    await _combat(db1, world, sid, scene_id, kael_init=20, bront_init=10, guard_hp=40)
    wid = await _open(db1, world, sid, scene_id, [world.kael_id], mode=WindowMode.COMBAT)
    await _submit(db1, wid, world.kael_id, raw="ฟันยาม",
                  fields={"action_target": "ยามเฝ้าประตู"})    # solo auto-ready
    pkg1 = await RoundResolver(db1, provider, SequenceRandomness(default=6)).resolve(window_id=wid)
    hp_after_first = pkg1.final_positions[f"npc:{world.guard_npc_id}"]["hp"]
    await db1.dispose()

    db2 = Database(url, echo=False)
    pkg2 = await RoundResolver(db2, provider, SequenceRandomness(default=6)).resolve(window_id=wid)
    # Same package; no second attack was applied to the guard.
    assert pkg2.final_positions[f"npc:{world.guard_npc_id}"]["hp"] == hp_after_first
    assert pkg2.damage == pkg1.damage
    await db2.dispose()
