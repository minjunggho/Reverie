"""ConcentrationService — deterministic concentration tracking (SRD 5.2.1; §14).

Invariants the engine enforces (never the LLM):
- at most ONE active concentration effect per character; starting a second ends
  the first (with its own event);
- taking damage while concentrating forces a CON save, DC = max(10, damage // 2),
  rolled by the server dice engine; failure ends the effect;
- Incapacitated (condition) and death end concentration immediately;
- voluntary ending is free.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.core.ids import entity_ref
from app.models.character import Character
from app.models.enums import EventType, Visibility
from app.models.progression import ActiveEffect
from app.services.events import EventService
from app.tabletop.rules.derive import save_bonus


class ConcentrationService:
    def __init__(self, session: AsyncSession, dice_engine=None) -> None:
        self.session = session
        self.dice = dice_engine
        self.events = EventService(session)

    async def current(self, character_id: str) -> ActiveEffect | None:
        return (
            await self.session.execute(
                select(ActiveEffect).where(
                    ActiveEffect.character_id == character_id,
                    ActiveEffect.requires_concentration.is_(True),
                    ActiveEffect.active.is_(True),
                )
            )
        ).scalars().first()

    async def begin(
        self, *, character: Character, name: str, spell_key: str | None = None,
        targets: list[str] | None = None, duration_minutes: int | None = None,
        started_game_time: int = 0, session_id: str | None = None,
        scene_id: str | None = None,
    ) -> ActiveEffect:
        if "incapacitated" in (character.conditions or []):
            raise ValidationError("an incapacitated creature cannot concentrate")
        # One concentration effect at most: starting a new one ends the old.
        existing = await self.current(character.id)
        if existing is not None:
            await self._end(existing, character, reason="replaced",
                            session_id=session_id, scene_id=scene_id)
        effect = ActiveEffect(
            campaign_id=character.campaign_id, character_id=character.id,
            name=name, spell_key=spell_key, requires_concentration=True,
            targets=targets or [], duration_minutes=duration_minutes,
            started_game_time=started_game_time,
        )
        self.session.add(effect)
        await self.session.flush()
        return effect

    async def on_damage_taken(
        self, *, character: Character, damage_total: int,
        session_id: str | None = None, scene_id: str | None = None,
    ) -> dict | None:
        """CON save DC max(10, dmg//2). Returns the save summary, or None if the
        character isn't concentrating."""
        effect = await self.current(character.id)
        if effect is None:
            return None
        if self.dice is None:
            raise ValidationError("concentration save requires the dice engine")
        dc = max(10, damage_total // 2)
        bonus = save_bonus(character, "con").total
        roll = self.dice.resolve_saving_throw(modifier=bonus, dc=dc, ability="con")
        passed = roll.outcome == "success"
        if not passed:
            await self._end(effect, character, reason="failed_save",
                            session_id=session_id, scene_id=scene_id)
        await self.events.record(
            campaign_id=character.campaign_id, session_id=session_id, scene_id=scene_id,
            event_type=EventType.ABILITY_CHECK_RESOLVED,
            actor_entity=entity_ref("character", character.id),
            visibility=Visibility.PARTY,
            mechanical_changes=roll.as_dict(),
            payload={"summary": f"เซฟสมาธิ ({effect.name}) — {'ผ่าน' if passed else 'หลุด'}",
                     "kind": "concentration_save", "dc": dc},
            narrative_significance=20,
        )
        return {"dc": dc, "passed": passed, "effect": effect.name,
                "natural_roll": roll.natural_roll, "total": roll.total}

    async def on_incapacitated(self, character: Character, *, session_id=None,
                               scene_id=None) -> None:
        effect = await self.current(character.id)
        if effect is not None:
            await self._end(effect, character, reason="incapacitated",
                            session_id=session_id, scene_id=scene_id)

    async def end_voluntarily(self, character: Character, *, session_id=None,
                              scene_id=None) -> bool:
        effect = await self.current(character.id)
        if effect is None:
            return False
        await self._end(effect, character, reason="voluntary",
                        session_id=session_id, scene_id=scene_id)
        return True

    async def end_all_for(self, character: Character, *, reason: str,
                          session_id=None, scene_id=None) -> None:
        effect = await self.current(character.id)
        if effect is not None:
            await self._end(effect, character, reason=reason,
                            session_id=session_id, scene_id=scene_id)

    async def _end(self, effect: ActiveEffect, character: Character, *, reason: str,
                   session_id=None, scene_id=None) -> None:
        effect.active = False
        await self.events.record(
            campaign_id=character.campaign_id, session_id=session_id, scene_id=scene_id,
            event_type=EventType.NPC_STATE_CHANGED,  # effect lifecycle event
            actor_entity=entity_ref("character", character.id),
            visibility=Visibility.PARTY,
            payload={"summary": f"{effect.name} สิ้นสุดลง", "reason": reason,
                     "kind": "effect_ended", "spell": effect.spell_key},
            narrative_significance=15,
        )
