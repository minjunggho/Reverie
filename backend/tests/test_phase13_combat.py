"""Phase 13 acceptance: initiative/turns/attack/damage/HP/end with server dice; one
interrupt (opportunity attack) pauses and resumes; action economy enforced; concise
narration."""
from __future__ import annotations

import pytest

from app.core.errors import RulesViolation
from app.core.randomness import SequenceRandomness
from app.models.enums import EventType, Visibility
from app.models.event import Event
from app.tabletop.combat import CombatantSpec, CombatService, format_combat_line
from app.tabletop.dice import DiceEngine
from tests.support.factories import build_world

from sqlalchemy import select


def _specs():
    return [
        CombatantSpec(entity_ref="character:kael", name="Kael", hp=9, max_hp=9, ac=14,
                      init_mod=3, attack_bonus=5, damage_die=6, damage_bonus=3, is_pc=True),
        CombatantSpec(entity_ref="npc:guard", name="ยาม", hp=11, max_hp=11, ac=13,
                      init_mod=0, attack_bonus=3, damage_die=8, damage_bonus=1, is_pc=False),
    ]


async def test_initiative_orders_by_roll(db):
    world = await build_world(db)
    # Kael rolls 10 (+3=13); guard rolls 18 (+0=18) -> guard acts first.
    dice = DiceEngine(SequenceRandomness([10, 18]))
    async with db.unit_of_work() as s:
        svc = CombatService(s, dice)
        enc = await svc.start_combat(campaign_id=world.campaign_id, specs=_specs())
        current = await svc.current_combatant(enc)
        assert current.name == "ยาม"  # higher initiative first
        # COMBAT_STARTED recorded.
        started = (
            await s.execute(select(Event).where(Event.event_type == EventType.COMBAT_STARTED.value))
        ).scalars().all()
        assert len(started) == 1 and started[0].visibility == Visibility.PARTY.value


async def test_attack_hits_applies_damage_and_hp(db):
    world = await build_world(db)
    # init: kael 18(+3=21), guard 5 -> kael first. Then attack roll 15(+5=20 vs AC13 hit),
    # damage die d6=6 (+3=9).
    dice = DiceEngine(SequenceRandomness([18, 5, 15, 6]))
    async with db.unit_of_work() as s:
        svc = CombatService(s, dice)
        enc = await svc.start_combat(campaign_id=world.campaign_id, specs=_specs())
        kael = await svc.current_combatant(enc)
        assert kael.name == "Kael"
        order = enc.initiative_order
        guard_id = order[1]
        outcome = await svc.attack(enc, attacker_id=kael.id, target_id=guard_id)
        assert outcome.hit and outcome.damage == 9
        assert outcome.hp_before == 11 and outcome.hp_after == 2
        # Concise line renders engine-owned numbers.
        line = format_combat_line("Kael", outcome)
        assert "HP 11 → 2" in line
        # ATTACK_RESOLVED + DAMAGE_APPLIED recorded.
        types = {
            e.event_type for e in (
                await s.execute(select(Event).where(Event.campaign_id == world.campaign_id))
            ).scalars()
        }
        assert EventType.ATTACK_RESOLVED.value in types
        assert EventType.DAMAGE_APPLIED.value in types


async def test_action_economy_blocks_second_action(db):
    world = await build_world(db)
    dice = DiceEngine(SequenceRandomness([18, 5, 15, 6, 15, 6]))
    async with db.unit_of_work() as s:
        svc = CombatService(s, dice)
        enc = await svc.start_combat(campaign_id=world.campaign_id, specs=_specs())
        kael = await svc.current_combatant(enc)
        guard_id = enc.initiative_order[1]
        await svc.attack(enc, attacker_id=kael.id, target_id=guard_id)
        with pytest.raises(RulesViolation):
            await svc.attack(enc, attacker_id=kael.id, target_id=guard_id)  # no action left


async def test_opportunity_attack_interrupt_pauses_and_resumes(db):
    world = await build_world(db)
    # Kael first (18). Guard makes an opportunity attack (interrupt) during Kael's turn.
    # guard OA roll 17(+3=20 vs AC14 hit), dmg d8=8(+1=9). Then Kael still has his action.
    dice = DiceEngine(SequenceRandomness([18, 5, 17, 8, 12, 6]))
    async with db.unit_of_work() as s:
        svc = CombatService(s, dice)
        enc = await svc.start_combat(campaign_id=world.campaign_id, specs=_specs())
        kael = await svc.current_combatant(enc)
        guard_id = enc.initiative_order[1]

        # Interrupt: guard's opportunity attack against Kael (not the guard's turn).
        oa = await svc.opportunity_attack(enc, reactor_id=guard_id, target_id=kael.id)
        assert oa.interrupt and oa.hit and oa.hp_after == 0  # 9 dmg vs 9 hp -> down
        # Guard's reaction is now spent.
        guard = await svc.get_combatant(guard_id)
        assert guard.has_reaction is False
        # The paused turn resumes: it is STILL Kael's turn and he still has his action.
        current = await svc.current_combatant(enc)
        assert current.id == kael.id and current.has_action is True


async def test_turn_advances_and_combat_ends(db):
    world = await build_world(db)
    # Kael first. Kael hits twice (nat 20s) to drop the guard, then combat is over.
    # Sequence: init[18,5], attack1[nat20, dmg6], attack2[nat20, dmg6].
    dice = DiceEngine(SequenceRandomness([18, 5, 20, 6, 20, 6]))
    async with db.unit_of_work() as s:
        svc = CombatService(s, dice)
        enc = await svc.start_combat(campaign_id=world.campaign_id, specs=_specs())
        kael = await svc.current_combatant(enc)
        guard_id = enc.initiative_order[1]
        # Big hits to drop the guard (11 hp): 6+3=9 then need more; do two rounds.
        await svc.attack(enc, attacker_id=kael.id, target_id=guard_id)  # 9 dmg -> 2 hp
        await svc.end_turn(enc)   # guard's turn (still alive)
        # guard attacks kael, but we only care about combat-over logic; end guard turn
        # without acting, back to Kael.
        await svc.end_turn(enc)   # back to Kael, round 2
        await svc.attack(enc, attacker_id=kael.id, target_id=guard_id)  # 9 dmg -> down
        assert await svc.is_over(enc) is True
        await svc.end_combat(enc, reason="enemies down")
        assert enc.status == "ended"
        ended = (
            await s.execute(select(Event).where(Event.event_type == EventType.COMBAT_ENDED.value))
        ).scalars().all()
        assert len(ended) == 1
