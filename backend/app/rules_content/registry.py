"""RulesRegistry — loads and validates the versioned rules content.

Read-only after load. Services and the derivation engine query it; nothing here
touches the database or the LLM.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.core.errors import RulesViolation

RULESET_ID = "srd521"
_CONTENT_DIR = Path(__file__).parent / "srd_5_2_1"

STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]


class _Def(BaseModel):
    ruleset_id: str = RULESET_ID
    definition_id: str
    definition_version: int = 1


class MaxFormula(BaseModel):
    """Data expression for a resource maximum (never code)."""
    kind: Literal["flat", "by_class_level", "ability_mod_min_1", "half_level_round_up"]
    value: int | None = None
    table: dict[str, int] = Field(default_factory=dict)
    ability: str | None = None


class ResourceDef(_Def):
    name: str
    name_th: str
    recharge: Literal["short_rest", "long_rest", "long_rest_cycle_after_short_rest"]
    max_formula: MaxFormula
    # For partial recharge on the *other* rest kind (e.g. Second Wind +1 on short rest).
    short_rest_partial: int = 0
    special_op: str | None = None  # e.g. "restore_slot_levels" (Arcane Recovery)


class SkillDef(_Def):
    name: str
    ability: str
    name_th: str
    explain_th: str


class SpellDef(_Def):
    name: str
    name_th_hint: str          # short Thai gloss, e.g. "ลูกไฟพุ่ง"
    level: int                 # 0 = cantrip
    school: str
    casting_time: str          # "action" | "bonus_action" | "reaction" | "1m" | "10m"
    range: str
    duration: str
    concentration: bool = False
    ritual: bool = False
    damage: str | None = None      # e.g. "1d10 fire"
    healing: str | None = None
    ux_category: str               # โจมตี/ป้องกัน/ควบคุม/สำรวจ/ภาพลวงตา/ฟื้นฟู/ใช้งาน
    mech_summary_th: str
    classes: list[str] = Field(default_factory=list)


class TraitDef(BaseModel):
    key: str
    name_th: str
    summary_th: str
    # Mechanical hooks the engine understands today (extend as slices land):
    resistances: list[str] = Field(default_factory=list)
    hp_per_level: int = 0
    darkvision: int = 0
    skill_choice: dict[str, Any] | None = None  # {"count": 1, "options": [...]|"any"}


class SpeciesDef(_Def):
    name: str
    name_th: str
    pitch_th: str              # why this species is an interesting fantasy
    speed: int
    traits: list[TraitDef] = Field(default_factory=list)


class BackgroundDef(_Def):
    name: str
    name_th: str
    pitch_th: str
    ability_options: list[str]             # the trio; player picks +2/+1 or +1/+1/+1
    skill_proficiencies: list[str]         # fixed two (2024 rules)
    tool_proficiency: str
    origin_feat: str                       # recorded grant; execution deferred
    equipment_th: list[str] = Field(default_factory=list)


class SpellcastingDef(BaseModel):
    ability: str
    cantrips_known: int = 0
    spellbook_size: int = 0                # wizard only
    prepared_count: int = 0
    spell_list: str = ""                   # class key used to filter SpellDef.classes


class BaseAC(BaseModel):
    kind: Literal["unarmored", "flat", "light", "medium"]
    value: int = 10          # flat AC, or armor base for light/medium
    dex_cap: int | None = None
    shield: bool = False


class FeatureDef(BaseModel):
    key: str
    name_th: str
    summary_th: str
    resource_id: str | None = None
    # Choice-granting features (e.g. Rogue Expertise at L1):
    expertise_choice: dict[str, Any] | None = None  # {"count": 2, "from": "proficient"}


class ClassDef(_Def):
    name: str
    name_th: str
    pitch_th: str
    hit_die: int
    saving_throws: list[str]
    skill_choices: dict[str, Any]          # {"count": n, "options": [...]|"any"}
    armor_training_th: str
    weapon_training_th: str
    base_ac: BaseAC
    primary_abilities: list[str]           # drives the recommended array arrangement
    features: list[FeatureDef] = Field(default_factory=list)
    spellcasting: SpellcastingDef | None = None
    concept_keywords: list[str] = Field(default_factory=list)  # Thai recommend hints


class RulesRegistry:
    def __init__(self, content_dir: Path = _CONTENT_DIR) -> None:
        self.classes: dict[str, ClassDef] = {}
        self.species: dict[str, SpeciesDef] = {}
        self.backgrounds: dict[str, BackgroundDef] = {}
        self.spells: dict[str, SpellDef] = {}
        self.resources: dict[str, ResourceDef] = {}
        self.skills: dict[str, SkillDef] = {}
        self._load(content_dir)

    def _load(self, content_dir: Path) -> None:
        def read(name: str) -> list[dict]:
            return json.loads((content_dir / name).read_text(encoding="utf-8"))

        for raw in read("classes.json"):
            d = ClassDef.model_validate(raw)
            self.classes[d.name] = d
        for raw in read("species.json"):
            d = SpeciesDef.model_validate(raw)
            self.species[d.name] = d
        for raw in read("backgrounds.json"):
            d = BackgroundDef.model_validate(raw)
            self.backgrounds[d.name] = d
        for raw in read("spells.json"):
            d = SpellDef.model_validate(raw)
            self.spells[d.name] = d
        for raw in read("resources.json"):
            d = ResourceDef.model_validate(raw)
            self.resources[d.definition_id] = d
        for raw in read("skills.json"):
            d = SkillDef.model_validate(raw)
            self.skills[d.name] = d

    # --- queries ---------------------------------------------------------------
    def get_class(self, name: str) -> ClassDef:
        d = self.classes.get((name or "").lower())
        if d is None:
            raise RulesViolation(f"unknown class: {name!r}")
        return d

    def get_species(self, name: str) -> SpeciesDef:
        d = self.species.get((name or "").lower())
        if d is None:
            raise RulesViolation(f"unknown species: {name!r}")
        return d

    def get_background(self, name: str) -> BackgroundDef:
        d = self.backgrounds.get((name or "").lower())
        if d is None:
            raise RulesViolation(f"unknown background: {name!r}")
        return d

    def get_spell(self, name: str) -> SpellDef:
        d = self.spells.get((name or "").lower())
        if d is None:
            raise RulesViolation(f"unknown spell: {name!r}")
        return d

    def get_resource(self, definition_id: str) -> ResourceDef:
        d = self.resources.get(definition_id)
        if d is None:
            raise RulesViolation(f"unknown resource: {definition_id!r}")
        return d

    def spells_for_class(self, class_name: str, level: int) -> list[SpellDef]:
        return sorted(
            (s for s in self.spells.values()
             if class_name in s.classes and s.level == level),
            key=lambda s: s.name,
        )

    def resolve_max(self, formula: MaxFormula, *, class_level: int = 1,
                    ability_mod: int = 0) -> int:
        if formula.kind == "flat":
            return int(formula.value or 0)
        if formula.kind == "by_class_level":
            keys = [int(k) for k in formula.table if int(k) <= class_level]
            return int(formula.table[str(max(keys))]) if keys else 0
        if formula.kind == "ability_mod_min_1":
            return max(1, ability_mod)
        if formula.kind == "half_level_round_up":
            return (class_level + 1) // 2
        raise RulesViolation(f"unknown max formula kind {formula.kind!r}")


@lru_cache(maxsize=1)
def get_registry() -> RulesRegistry:
    return RulesRegistry()
