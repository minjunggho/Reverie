"""Warlock — Pact Magic, Eldritch Invocations, Pact Boon.

Pact slots are already ResourceState pools with short-rest recharge (the shared
ResourceEngine + RestService handle scaling and recovery). This module adds the
warlock's CHOICES: a catalog of Invocations with typed prerequisites and Pact
Boons, granted as CharacterGrant rows (the same persistence every feature uses).
No new spell/resource/persistence system.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RulesViolation
from app.models.character import Character
from app.models.progression import CharacterGrant, CharacterSpell

PACT_SLOTS = "resource:pact_slots"


@dataclass
class Invocation:
    key: str
    name_th: str
    min_level: int = 1
    requires_pact: str | None = None      # "blade" | "tome" | "chain" | None
    requires_cantrip: str | None = None   # e.g. "eldritch_blast"


# A representative catalog with real 2024 prerequisites (not exhaustive).
INVOCATIONS: dict[str, Invocation] = {
    "agonizing_blast": Invocation("agonizing_blast", "ลำแสงทรมาน (Agonizing Blast)",
                                  requires_cantrip="eldritch_blast"),
    "armor_of_shadows": Invocation("armor_of_shadows", "เกราะเงา (Armor of Shadows)"),
    "devils_sight": Invocation("devils_sight", "ตาปีศาจ (Devil's Sight)"),
    "mask_of_many_faces": Invocation("mask_of_many_faces", "หน้ากากพันหน้า"),
    "book_of_ancient_secrets": Invocation("book_of_ancient_secrets",
                                          "ตำราความลับโบราณ", requires_pact="tome"),
    "thirsting_blade": Invocation("thirsting_blade", "ดาบกระหาย (Thirsting Blade)",
                                  min_level=5, requires_pact="blade"),
    "voice_of_the_chain_master": Invocation("voice_of_the_chain_master",
                                            "เสียงเจ้านายโซ่", requires_pact="chain"),
}

PACT_BOONS = {
    "blade": "พันธะดาบ (Pact of the Blade)",
    "tome": "พันธะตำรา (Pact of the Tome)",
    "chain": "พันธะโซ่ (Pact of the Chain)",
}


class WarlockService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _known_cantrips(self, character: Character) -> set[str]:
        rows = (await self.session.execute(
            select(CharacterSpell).where(
                CharacterSpell.character_id == character.id,
                CharacterSpell.kind == "cantrip"))).scalars()
        return {r.spell_key for r in rows}

    async def _pact(self, character: Character) -> str | None:
        row = (await self.session.execute(
            select(CharacterGrant).where(
                CharacterGrant.character_id == character.id,
                CharacterGrant.grant_type == "pact_boon"))).scalars().first()
        return row.key if row else None

    async def choose_pact_boon(self, character: Character, boon: str) -> CharacterGrant:
        if boon not in PACT_BOONS:
            raise RulesViolation(f"ไม่มีพันธะ {boon!r}")
        grant = CharacterGrant(
            character_id=character.id, grant_type="pact_boon", key=boon,
            name_th=PACT_BOONS[boon], source_type="CLASS", source_key="class:warlock")
        self.session.add(grant)
        await self.session.flush()
        return grant

    async def can_take_invocation(self, character: Character, key: str) -> tuple[bool, str]:
        inv = INVOCATIONS.get(key)
        if inv is None:
            return False, f"ไม่มี Invocation ชื่อ {key!r}"
        if character.level < inv.min_level:
            return False, f"ต้องถึงเลเวล {inv.min_level}"
        if inv.requires_cantrip and inv.requires_cantrip not in await self._known_cantrips(character):
            return False, f"ต้องรู้คาถา {inv.requires_cantrip}"
        if inv.requires_pact and inv.requires_pact != await self._pact(character):
            return False, f"ต้องมีพันธะ {inv.requires_pact}"
        return True, "ok"

    async def take_invocation(self, character: Character, key: str) -> CharacterGrant:
        ok, reason = await self.can_take_invocation(character, key)
        if not ok:
            raise RulesViolation(f"เลือก Invocation {key!r} ไม่ได้: {reason}")
        grant = CharacterGrant(
            character_id=character.id, grant_type="invocation", key=key,
            name_th=INVOCATIONS[key].name_th, source_type="CLASS", source_key="class:warlock")
        self.session.add(grant)
        await self.session.flush()
        return grant
