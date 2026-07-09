"""Basic combat engine (§22).

Correct-but-limited: start, initiative (server roll), ordering, turns/rounds, a basic
attack + damage + HP, action economy (one action/turn, one reaction/round), an
opportunity-attack interrupt that pauses and resumes the turn, and combat end. Every
number is produced by the deterministic dice engine; the LLM never rolls.

Operates within the caller's transaction (flushes, does not commit). Narration is
concise by default — `format_combat_line` renders the engine-owned mechanical line.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, RulesViolation
from app.models.combat import Combatant, CombatEncounter
from app.models.enums import EventType, Visibility
from app.services.events import EventService
from app.tabletop.dice import DiceEngine


@dataclass
class CombatantSpec:
    entity_ref: str
    name: str
    hp: int
    max_hp: int
    ac: int
    init_mod: int = 0
    attack_bonus: int = 0
    damage_die: int = 6
    damage_bonus: int = 0
    is_pc: bool = False


@dataclass
class AttackOutcome:
    attacker: str
    target: str
    attack_total: int
    natural_roll: int
    target_ac: int
    hit: bool
    damage: int
    hp_before: int
    hp_after: int
    target_down: bool
    interrupt: bool = False
    notes: list[str] = field(default_factory=list)


def format_combat_line(name: str, outcome: AttackOutcome) -> str:
    """Concise, engine-owned combat line (Thai flavour is added separately if desired)."""
    verb = "โดน" if outcome.hit else "พลาด"
    if not outcome.hit:
        return f"{name} โจมตี: {outcome.attack_total}  |  AC {outcome.target_ac} — {verb}"
    return (
        f"{name} โจมตี: {outcome.attack_total}  |  AC {outcome.target_ac} — {verb}  |  "
        f"ดาเมจ: {outcome.damage}  |  HP {outcome.hp_before} → {outcome.hp_after}"
    )


class CombatService:
    def __init__(self, session: AsyncSession, dice: DiceEngine) -> None:
        self.session = session
        self.dice = dice
        self.events = EventService(session)

    # --- lifecycle -----------------------------------------------------------
    async def start_combat(
        self, *, campaign_id: str, specs: list[CombatantSpec],
        session_id: str | None = None, scene_id: str | None = None,
    ) -> CombatEncounter:
        encounter = CombatEncounter(
            campaign_id=campaign_id, session_id=session_id, scene_id=scene_id,
            round=1, turn_index=0, status="active",
        )
        self.session.add(encounter)
        await self.session.flush()

        built: list[Combatant] = []
        for spec in specs:
            init_roll, _ = self.dice.roll_d20()
            c = Combatant(
                encounter_id=encounter.id, entity_ref=spec.entity_ref, name=spec.name,
                initiative=init_roll + spec.init_mod, hp=spec.hp, max_hp=spec.max_hp, ac=spec.ac,
                attack_bonus=spec.attack_bonus, damage_die=spec.damage_die,
                damage_bonus=spec.damage_bonus, is_pc=spec.is_pc,
            )
            self.session.add(c)
            built.append(c)
        await self.session.flush()

        # Highest initiative first; ties broken by PCs, then name for determinism.
        built.sort(key=lambda c: (c.initiative, c.is_pc, c.name), reverse=True)
        encounter.initiative_order = [c.id for c in built]
        built[0].has_action = True
        await self.events.record(
            campaign_id=campaign_id, session_id=session_id, scene_id=scene_id,
            event_type=EventType.COMBAT_STARTED, actor_entity="system",
            visibility=Visibility.PARTY,
            payload={"order": [{"name": c.name, "initiative": c.initiative} for c in built],
                     "summary": "การต่อสู้เริ่มขึ้น"},
            narrative_significance=30,
        )
        return encounter

    async def current_combatant(self, encounter: CombatEncounter) -> Combatant:
        cid = encounter.initiative_order[encounter.turn_index]
        c = await self.session.get(Combatant, cid)
        if c is None:
            raise NotFoundError("current combatant missing")
        return c

    async def get_combatant(self, combatant_id: str) -> Combatant:
        c = await self.session.get(Combatant, combatant_id)
        if c is None:
            raise NotFoundError(f"combatant {combatant_id} not found")
        return c

    async def _combatants(self, encounter: CombatEncounter) -> list[Combatant]:
        rows = (
            await self.session.execute(
                select(Combatant).where(Combatant.encounter_id == encounter.id)
            )
        ).scalars()
        return list(rows)

    # --- actions -------------------------------------------------------------
    async def attack(
        self, encounter: CombatEncounter, *, attacker_id: str, target_id: str,
    ) -> AttackOutcome:
        attacker = await self.get_combatant(attacker_id)
        current = await self.current_combatant(encounter)
        if attacker.id != current.id:
            raise RulesViolation("not this combatant's turn")
        if not attacker.has_action:
            raise RulesViolation("no action left this turn")
        attacker.has_action = False
        return await self._resolve_attack(encounter, attacker, target_id, interrupt=False)

    async def opportunity_attack(
        self, encounter: CombatEncounter, *, reactor_id: str, target_id: str,
    ) -> AttackOutcome:
        """An interrupt: a reacting combatant strikes out of turn, pausing the current
        turn. Consumes the reactor's reaction; the paused turn then resumes."""
        reactor = await self.get_combatant(reactor_id)
        if not reactor.has_reaction:
            raise RulesViolation("no reaction available")
        reactor.has_reaction = False
        outcome = await self._resolve_attack(encounter, reactor, target_id, interrupt=True)
        outcome.notes.append("โจมตีสวน (ขัดจังหวะ) แล้วเทิร์นเดิมดำเนินต่อ")
        return outcome

    async def _resolve_attack(
        self, encounter: CombatEncounter, attacker: Combatant, target_id: str, *, interrupt: bool,
    ) -> AttackOutcome:
        target = await self.get_combatant(target_id)
        if not target.alive:
            raise RulesViolation("target is already down")

        roll = self.dice.resolve_attack(attack_modifier=attacker.attack_bonus, target_ac=target.ac)
        await self.events.record(
            campaign_id=encounter.campaign_id, session_id=encounter.session_id,
            scene_id=encounter.scene_id, event_type=EventType.ATTACK_RESOLVED,
            actor_entity=attacker.entity_ref, target_entities=[target.entity_ref],
            visibility=Visibility.PARTY, mechanical_changes=roll.as_dict(),
            payload={"interrupt": interrupt}, narrative_significance=15,
        )

        damage = 0
        hp_before = target.hp
        if roll.outcome == "success":
            damage, _ = self.dice.resolve_damage(dice=[attacker.damage_die],
                                                 flat_modifier=attacker.damage_bonus)
            target.hp = max(0, target.hp - damage)
            await self.events.record(
                campaign_id=encounter.campaign_id, session_id=encounter.session_id,
                scene_id=encounter.scene_id, event_type=EventType.DAMAGE_APPLIED,
                actor_entity=attacker.entity_ref, target_entities=[target.entity_ref],
                visibility=Visibility.PARTY,
                mechanical_changes={"hp": {"from": hp_before, "to": target.hp}, "damage": damage},
                narrative_significance=15,
            )
        target_down = target.hp <= 0
        if target_down:
            target.alive = False

        return AttackOutcome(
            attacker=attacker.entity_ref, target=target.entity_ref,
            attack_total=roll.total, natural_roll=roll.natural_roll, target_ac=target.ac,
            hit=roll.outcome == "success", damage=damage, hp_before=hp_before,
            hp_after=target.hp, target_down=target_down, interrupt=interrupt,
        )

    async def end_turn(self, encounter: CombatEncounter) -> Combatant:
        n = len(encounter.initiative_order)
        encounter.turn_index += 1
        if encounter.turn_index >= n:
            encounter.turn_index = 0
            encounter.round += 1
            # New round: refresh reactions for everyone.
            for c in await self._combatants(encounter):
                c.has_reaction = True
        encounter.version += 1
        current = await self.current_combatant(encounter)
        current.has_action = True
        # Skip downed combatants automatically.
        if not current.alive:
            return await self.end_turn(encounter)
        return current

    async def is_over(self, encounter: CombatEncounter) -> bool:
        combatants = await self._combatants(encounter)
        pcs_up = any(c.alive for c in combatants if c.is_pc)
        foes_up = any(c.alive for c in combatants if not c.is_pc)
        return not (pcs_up and foes_up)

    async def end_combat(self, encounter: CombatEncounter, *, reason: str = "resolved") -> None:
        encounter.status = "ended"
        encounter.version += 1
        await self.events.record(
            campaign_id=encounter.campaign_id, session_id=encounter.session_id,
            scene_id=encounter.scene_id, event_type=EventType.COMBAT_ENDED,
            actor_entity="system", visibility=Visibility.PARTY,
            payload={"reason": reason, "round": encounter.round, "summary": "จบการต่อสู้"},
            narrative_significance=25,
        )
