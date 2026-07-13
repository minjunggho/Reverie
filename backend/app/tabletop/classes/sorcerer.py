"""Sorcerer — Font of Magic (Sorcery Points ⇄ spell slots) + Metamagic.

Built on the shared ResourceEngine: Sorcery Points and spell slots are both
ResourceState pools, so conversion is just an atomic spend on one + restore on the
other, and Metamagic is an atomic Sorcery-Point spend that returns a modifier the
SpellEngine applies. No new resource system.

SRD 5.2.1 Font of Magic conversion table (Sorcery Points → a spell slot):
  1st = 2, 2nd = 3, 3rd = 5, 4th = 6, 5th = 7.
Creating Sorcery Points from a slot yields Sorcery Points equal to the slot level.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RulesViolation, ValidationError
from app.models.character import Character
from app.rules_content import get_registry
from app.tabletop.resources import ResourceEngine

SORCERY_POINTS = "resource:sorcery_points"

# Sorcery Points required to CREATE a slot of the given spell level.
_SP_COST_FOR_SLOT = {1: 2, 2: 3, 3: 5, 4: 6, 5: 7}


@dataclass
class Metamagic:
    key: str
    name_th: str
    sp_cost: int
    effect: str          # engine-usable descriptor the SpellEngine can honor


# The core Metamagic options (2024). sp_cost is what Metamagic charges; the effect
# string is a stable key a caster/UI/engine can act on.
METAMAGIC: dict[str, Metamagic] = {
    "quickened": Metamagic("quickened", "เร่งร่าย (Quickened Spell)", 2, "cast_time=bonus_action"),
    "twinned": Metamagic("twinned", "ร่ายคู่ (Twinned Spell)", 1, "extra_target=1"),
    "careful": Metamagic("careful", "ระวังภัย (Careful Spell)", 1, "allies_auto_save"),
    "subtle": Metamagic("subtle", "ร่ายแนบเนียน (Subtle Spell)", 1, "no_components"),
    "distant": Metamagic("distant", "ร่ายไกล (Distant Spell)", 1, "range_double"),
    "empowered": Metamagic("empowered", "เพิ่มพลัง (Empowered Spell)", 1, "reroll_damage"),
    "heightened": Metamagic("heightened", "กดดัน (Heightened Spell)", 2, "save_disadvantage"),
}


def sorcery_points_max(level: int) -> int:
    """2024: a sorcerer gains Sorcery Points equal to class level, from level 2."""
    return 0 if level < 2 else level


class SorceryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.resources = ResourceEngine(session)
        self.reg = get_registry()

    async def _slot_resource(self, character: Character, slot_level: int) -> str:
        cls = self.reg.get_class(character.char_class)
        rid = (cls.spellcasting.slot_resources.get(str(slot_level))
               if cls.spellcasting else None)
        if rid is None:
            raise ValidationError(f"ไม่มีช่องเวทระดับ {slot_level} สำหรับคลาสนี้")
        return rid

    async def create_slot_from_points(self, character: Character, slot_level: int):
        """Spend Sorcery Points to gain a spell slot of `slot_level` (atomic)."""
        cost = _SP_COST_FOR_SLOT.get(slot_level)
        if cost is None:
            raise ValidationError(f"แปลงเป็นช่องเวทระดับ {slot_level} ไม่ได้")
        slot_rid = await self._slot_resource(character, slot_level)
        await self.resources.spend(character.id, SORCERY_POINTS, cost)   # rejects if insufficient
        return await self.resources.restore(character.id, slot_rid, 1)

    async def create_points_from_slot(self, character: Character, slot_level: int):
        """Spend a spell slot to gain Sorcery Points equal to its level (atomic)."""
        slot_rid = await self._slot_resource(character, slot_level)
        await self.resources.spend(character.id, slot_rid, 1)            # rejects if none left
        return await self.resources.restore(character.id, SORCERY_POINTS, slot_level)

    async def apply_metamagic(self, character: Character, option_key: str) -> Metamagic:
        """Pay a Metamagic option's Sorcery-Point cost and return its effect
        descriptor for the SpellEngine/UI to honor. Atomic; rejects if too few SP."""
        option = METAMAGIC.get(option_key)
        if option is None:
            raise RulesViolation(f"ไม่มี Metamagic ชื่อ {option_key!r}")
        await self.resources.spend(character.id, SORCERY_POINTS, option.sp_cost)
        return option
