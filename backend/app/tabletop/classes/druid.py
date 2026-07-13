"""Druid — Wild Shape with AUTHORITATIVE form data (never LLM invention).

A druid may only assume a form defined in the beast-form content (`BeastFormDef`),
gated by druid level (CR gate). Transforming is tracked as an ActiveEffect that
snapshots the form's stats; the druid's own sheet is NOT mutated — the form's HP is
a separate pool (temp HP, 2024-style) so reverting restores the druid cleanly.
Wild Shape uses come from the shared ResourceEngine (short-rest recharge).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RulesViolation
from app.models.character import Character
from app.models.enums import EventType, Visibility
from app.models.progression import ActiveEffect
from app.rules_content import get_registry
from app.rules_content.registry import BeastFormDef

WILD_SHAPE = "resource:wild_shape"
_EFFECT = "Wild Shape"


@dataclass
class ShapeResult:
    form_key: str
    name_th: str
    form_hp: int
    ac: int
    line_th: str


class WildShapeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.reg = get_registry()

    async def current_form(self, character_id: str) -> ActiveEffect | None:
        return (await self.session.execute(select(ActiveEffect).where(
            ActiveEffect.character_id == character_id, ActiveEffect.name == _EFFECT,
            ActiveEffect.active.is_(True)))).scalars().first()

    def legal_forms(self, druid_level: int) -> list[BeastFormDef]:
        return self.reg.legal_beast_forms(druid_level)

    async def transform(self, character: Character, form_key: str) -> ShapeResult:
        """Assume an authoritative beast form. The form MUST be one the character's
        level allows — an unknown or too-high-CR form is refused (the LLM can't
        conjure a form or its numbers). Spends one Wild Shape use; sets the form's
        HP as a temporary pool."""
        form = self.reg.get_beast_form(form_key)                 # raises if unknown
        legal = {f.key for f in self.legal_forms(character.level)}
        if form.key not in legal:
            raise RulesViolation(
                f"เลเวล {character.level} ยังแปลงร่างเป็น {form.name_th} ไม่ได้ "
                f"(ต้องถึงเลเวล {form.max_druid_level})")
        from app.tabletop.resources import ResourceEngine

        await ResourceEngine(self.session).spend(character.id, WILD_SHAPE, 1)  # rejects if none
        await self._revert_silent(character)                     # never stack two forms
        effect = ActiveEffect(
            campaign_id=character.campaign_id, character_id=character.id, name=_EFFECT,
            requires_concentration=False, active=True,
            data={"kind": "wild_shape", "form": form.key, "name_th": form.name_th,
                  "ac": form.ac, "form_hp": form.form_hp, "speed": form.speed,
                  "attack": {"name_th": form.attack_name_th, "bonus": form.attack_bonus,
                             "damage": form.damage},
                  "str": form.str_score, "dex": form.dex_score, "con": form.con_score})
        self.session.add(effect)
        # 2024: the form's HP is a separate pool on top of your own (temp HP).
        character.temp_hp = form.form_hp
        await self.session.flush()
        await self._record(character, f"แปลงร่างเป็น {form.name_th}")
        return ShapeResult(form.key, form.name_th, form.form_hp, form.ac,
                           f"แปลงร่างเป็น {form.name_th} (AC {form.ac}, HP ฟอร์ม {form.form_hp})")

    async def revert(self, character: Character) -> bool:
        """Return to true form: end the effect and drop the form's HP pool. The
        druid's own HP was never touched."""
        effect = await self.current_form(character.id)
        if effect is None:
            return False
        effect.active = False
        character.temp_hp = 0
        await self.session.flush()
        await self._record(character, "กลับคืนร่างเดิม")
        return True

    async def _revert_silent(self, character: Character) -> None:
        effect = await self.current_form(character.id)
        if effect is not None:
            effect.active = False

    async def _record(self, character: Character, summary: str) -> None:
        from app.core.ids import entity_ref
        from app.services.events import EventService

        await EventService(self.session).record(
            campaign_id=character.campaign_id, event_type=EventType.FEATURE_USED,
            actor_entity=entity_ref("character", character.id), visibility=Visibility.PARTY,
            payload={"feature": "wild_shape", "summary": summary}, narrative_significance=16)
