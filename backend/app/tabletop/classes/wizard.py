"""Wizard — spellbook learning + ritual casting (+ Arcane Recovery, which the
ResourceEngine + RestService already handle via the resource definition).

Spells are CharacterSpell rows (kind="book"); the daily prepared subset is the
`prepared` flag. Learning a spell adds a book row; preparing sets the flag. Ritual
casting is the SpellEngine's slot-free path for a ritual spell. No new persistence.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RulesViolation
from app.models.character import Character
from app.models.progression import CharacterSpell
from app.rules_content import get_registry

ARCANE_RECOVERY = "resource:arcane_recovery"
# Free spellbook spells added when a wizard levels up (2024: 2 per level).
SPELLS_LEARNED_PER_LEVEL = 2


class WizardSpellbook:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.reg = get_registry()

    async def contents(self, character: Character) -> list[str]:
        rows = (await self.session.execute(
            select(CharacterSpell).where(
                CharacterSpell.character_id == character.id,
                CharacterSpell.kind == "book"))).scalars()
        return [r.spell_key for r in rows]

    async def learn(self, character: Character, spell_key: str) -> CharacterSpell:
        """Add a spell to the spellbook (copying/leveling up). It must be a real
        wizard spell and not already present."""
        spell = self.reg.get_spell(spell_key)          # raises on unknown
        if "wizard" not in spell.classes:
            raise RulesViolation(f"{spell.name_th_hint} ไม่อยู่ในรายการเวทของ Wizard")
        existing = (await self.session.execute(
            select(CharacterSpell).where(
                CharacterSpell.character_id == character.id,
                CharacterSpell.spell_key == spell.name,
                CharacterSpell.kind == "book"))).scalars().first()
        if existing is not None:
            raise RulesViolation(f"{spell.name_th_hint} อยู่ในตำราแล้ว")
        row = CharacterSpell(character_id=character.id, spell_key=spell.name,
                             kind="book", prepared=False, source_type="CLASS",
                             source_key="class:wizard")
        self.session.add(row)
        await self.session.flush()
        return row

    async def prepare(self, character: Character, spell_keys: list[str]) -> None:
        """Set the daily prepared subset from the spellbook (INT mod + level)."""
        book = {r.spell_key: r for r in (await self.session.execute(
            select(CharacterSpell).where(
                CharacterSpell.character_id == character.id,
                CharacterSpell.kind == "book"))).scalars()}
        wanted = {self.reg.get_spell(k).name for k in spell_keys}
        illegal = wanted - set(book)
        if illegal:
            raise RulesViolation(f"เตรียมคาถาที่ไม่มีในตำราไม่ได้: {sorted(illegal)}")
        for key, row in book.items():
            row.prepared = key in wanted
        await self.session.flush()
