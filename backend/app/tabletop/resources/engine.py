"""ResourceEngine — authoritative spend/restore for every limited-use capability.

"Does Veskan have a slot left?" is answered by ResourceState rows, never by
narrative memory. Operates inside the caller's transaction (flush, no commit).
Max values come from registry formulas; recharge semantics from the definition:

- long_rest: full on long rest
- short_rest_partial (definition field): +N on short rest (e.g. Second Wind)
- long_rest_cycle_after_short_rest: usable after a SHORT rest, but only once per
  long-rest cycle (Arcane Recovery) — restored by the long rest itself.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RulesViolation
from app.models.character import Character
from app.models.progression import ResourceState
from app.rules_content import get_registry
from app.tabletop.rules.core import ability_modifier


class ResourceEngine:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.registry = get_registry()

    async def grant(self, character: Character, resource_id: str) -> ResourceState:
        d = self.registry.get_resource(resource_id)
        mod = 0
        if d.max_formula.ability:
            mod = ability_modifier(character.ability_score(d.max_formula.ability))
        max_value = self.registry.resolve_max(
            d.max_formula, class_level=character.level, ability_mod=mod
        )
        state = ResourceState(
            character_id=character.id, resource_id=resource_id,
            current=max_value, max_value=max_value,
        )
        self.session.add(state)
        await self.session.flush()
        return state

    async def get(self, character_id: str, resource_id: str) -> ResourceState | None:
        return (
            await self.session.execute(
                select(ResourceState).where(
                    ResourceState.character_id == character_id,
                    ResourceState.resource_id == resource_id,
                )
            )
        ).scalars().first()

    async def list_for(self, character_id: str) -> list[ResourceState]:
        return list(
            (
                await self.session.execute(
                    select(ResourceState).where(ResourceState.character_id == character_id)
                )
            ).scalars()
        )

    async def spend(self, character_id: str, resource_id: str, amount: int = 1) -> ResourceState:
        state = await self.get(character_id, resource_id)
        if state is None:
            raise RulesViolation(f"character has no resource {resource_id!r}")
        if state.current < amount:
            d = self.registry.get_resource(resource_id)
            raise RulesViolation(f"{d.name_th} หมดแล้ว ({state.current}/{state.max_value})")
        state.current -= amount
        return state

    async def restore(self, character_id: str, resource_id: str, amount: int) -> ResourceState:
        state = await self.get(character_id, resource_id)
        if state is None:
            raise RulesViolation(f"character has no resource {resource_id!r}")
        state.current = min(state.max_value, state.current + amount)
        return state

    # --- rest recharges (called by RestService) --------------------------------
    async def apply_short_rest(self, character_id: str) -> list[str]:
        """Partial recharges only. Returns Thai notes of what recharged."""
        notes: list[str] = []
        for state in await self.list_for(character_id):
            d = self.registry.get_resource(state.resource_id)
            if d.short_rest_partial > 0 and state.current < state.max_value:
                state.current = min(state.max_value, state.current + d.short_rest_partial)
                notes.append(f"{d.name_th} +{d.short_rest_partial}")
            if d.recharge == "short_rest" and state.current < state.max_value:
                state.current = state.max_value
                notes.append(f"{d.name_th} เต็ม")
        return notes

    async def apply_long_rest(self, character_id: str) -> list[str]:
        notes: list[str] = []
        for state in await self.list_for(character_id):
            if state.current < state.max_value:
                state.current = state.max_value
                d = self.registry.get_resource(state.resource_id)
                notes.append(f"{d.name_th} เต็ม")
        return notes
