"""SubclassService — subclass selection, validation, persistence, feature grants.

Subclass features use the SAME shared systems as everything else: CharacterGrant
rows for features, ResourceEngine for granted resources, CharacterSpell for
always-prepared subclass spells. `planned_subclass` (a creation-time narrative
preference) is NEVER mechanical; only `active_subclass` — set at the class's
`subclass_level` after confirmation — grants anything.

Idempotent: selecting/granting twice (a retry or a repeated level-up) never
double-grants; a foreign or unknown subclass is rejected.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RulesViolation
from app.models.character import Character
from app.models.progression import CharacterGrant, CharacterSpell
from app.rules_content import get_registry
from app.rules_content.registry import SubclassDef


@dataclass
class SubclassChoice:
    key: str
    name_th: str
    pitch_th: str


class SubclassService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.reg = get_registry()

    def selection_level(self, char_class: str) -> int:
        """The authoritative level at which THIS class chooses a subclass (never
        assume 3 — read the class definition)."""
        return self.reg.get_class(char_class).subclass_level

    def legal_subclasses(self, char_class: str) -> list[SubclassChoice]:
        cls = self.reg.get_class(char_class)
        return [SubclassChoice(s.name, s.name_th, s.pitch_th)
                for s in self.reg.subclasses_for_class(cls.name)]

    def requires_selection(self, character: Character) -> bool:
        """True when the character has reached its subclass level but has no ACTIVE
        subclass yet — level-up must not complete until this is resolved."""
        return (character.level >= self.selection_level(character.char_class)
                and not character.active_subclass)

    def _validate(self, character: Character, subclass_key: str) -> SubclassDef:
        key = (subclass_key or "").lower()
        sub = self.reg.subclasses.get(key)
        if sub is None:
            raise RulesViolation(f"ไม่มี subclass ชื่อ {subclass_key!r}")
        if sub.parent_class != character.char_class.lower():
            raise RulesViolation(
                f"{sub.name_th} เป็นของคลาส {sub.parent_class} ไม่ใช่ {character.char_class}")
        if character.level < sub.selection_level:
            raise RulesViolation(
                f"ต้องถึงเลเวล {sub.selection_level} ก่อนจึงเลือก subclass ได้")
        return sub

    async def select_subclass(self, character: Character, subclass_key: str) -> dict:
        """Validate + persist the ACTIVE subclass and grant its level-appropriate
        features (idempotent). A planned_subclass does not become active without
        this explicit call."""
        sub = self._validate(character, subclass_key)
        character.active_subclass = sub.name
        notes = await self._grant_features(character, sub)
        await self.session.flush()
        return {"subclass": sub.name, "name_th": sub.name_th, "notes": notes}

    async def _grant_features(self, character: Character, sub: SubclassDef) -> list[str]:
        from app.tabletop.resources import ResourceEngine

        engine = ResourceEngine(self.session)
        granted = {g.key for g in (await self.session.execute(
            select(CharacterGrant).where(
                CharacterGrant.character_id == character.id,
                CharacterGrant.grant_type == "subclass_feature"))).scalars()}
        known_spells = {r.spell_key for r in (await self.session.execute(
            select(CharacterSpell).where(
                CharacterSpell.character_id == character.id))).scalars()}
        notes: list[str] = []
        for feat in sub.features_at(character.level):
            if feat.key in granted:
                continue                                     # never double-grant
            self.session.add(CharacterGrant(
                character_id=character.id, grant_type="subclass_feature", key=feat.key,
                name_th=feat.name_th, source_type="SUBCLASS",
                source_key=f"subclass:{sub.name}"))
            notes.append(feat.name_th)
            if feat.resource_id:
                if await engine.get(character.id, feat.resource_id) is None:
                    await engine.grant(character, feat.resource_id)
            for spell_key in feat.grants_spells:
                if spell_key not in known_spells:
                    self.session.add(CharacterSpell(
                        character_id=character.id, spell_key=spell_key, kind="known",
                        prepared=True, source_type="SUBCLASS",
                        source_key=f"subclass:{sub.name}"))
                    known_spells.add(spell_key)
        return notes

    async def subclass_features(self, character: Character) -> list[SubclassChoice]:
        """The active subclass's features the character currently has (for the sheet)."""
        if not character.active_subclass:
            return []
        sub = self.reg.subclasses.get(character.active_subclass.lower())
        if sub is None:
            return []
        return [SubclassChoice(f.key, f.name_th, f.summary_th)
                for f in sub.features_at(character.level)]
