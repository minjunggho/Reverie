"""RulesRegistry — loads and validates the versioned rules content.

Read-only after load. Services and the derivation engine query it; nothing here
touches the database or the LLM.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field, ValidationError as PydanticValidationError

from app.core.errors import RulesViolation
from app.rules_content.choice_names import (
    ChoiceOption,
    ChoiceResolution,
    normalize_choice_name,
    resolve_choice_name,
)

RULESET_ID = "srd521"
_CONTENT_DIR = Path(__file__).parent / "srd_5_2_1"
_MANIFEST_FILE = "manifest.json"

# These are class features, not spells, even if a malformed content pack labels
# them with spell-like timing or healing data.
_KNOWN_CLASS_FEATURE_NAMES = frozenset({"lay on hands"})

STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]


class _Def(BaseModel):
    ruleset_id: str = RULESET_ID
    definition_id: str
    definition_version: int = 1
    source_id: str | None = None
    source_kind: str | None = None
    content_status: str | None = None


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
    name_en: str | None = None
    name_th_hint: str          # short Thai gloss, e.g. "ลูกไฟพุ่ง"
    aliases: list[str] = Field(default_factory=list)
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
    # Optional explicit UI declaration.  When present it must be a subset of the
    # authoritative legal ``classes`` list or startup validation fails.
    display_classes: list[str] | None = None
    content_type: Literal["spell", "class_feature"] = "spell"

    @property
    def display_name_en(self) -> str:
        return self.name_en or self.name.replace("_", " ").title()


class RulesContentManifest(BaseModel):
    ruleset_id: str
    backend_rules_content_version: str
    ui_rules_content_version: str
    selectable_classes: list[str]


class TraitDef(BaseModel):
    key: str
    name_th: str
    summary_th: str
    # Mechanical hooks the engine understands today (extend as slices land):
    resistances: list[str] = Field(default_factory=list)
    hp_per_level: int = 0
    darkvision: int = 0
    skill_choice: dict[str, Any] | None = None  # {"count": 1, "options": [...]|"any"}
    implementation_status: str | None = None


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
    # Honest engine-support declaration. Only FULLY_SUPPORTED classes are
    # selectable in character creation; unfinished content stays in the pack
    # (never deleted) but is never offered as playable.
    support_status: Literal[
        "FULLY_SUPPORTED", "PARTIALLY_SUPPORTED", "UNSUPPORTED"
    ] = "UNSUPPORTED"
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


class SubclassDef(_Def):
    name: str
    name_th: str
    pitch_th: str
    parent_class: str
    selection_level: int = 3
    concept_keywords: list[str] = Field(default_factory=list)
    features: list[dict[str, Any]] = Field(default_factory=list)
    implementation_status: str = "planned"


class RulesRegistry:
    def __init__(self, content_dir: Path = _CONTENT_DIR) -> None:
        self.manifest: RulesContentManifest
        self.rules_content_version = "unknown"
        self.selectable_classes: tuple[str, ...] = ()
        self.classes: dict[str, ClassDef] = {}
        self.subclasses: dict[str, SubclassDef] = {}
        self.species: dict[str, SpeciesDef] = {}
        self.backgrounds: dict[str, BackgroundDef] = {}
        self.spells: dict[str, SpellDef] = {}
        self.resources: dict[str, ResourceDef] = {}
        self.skills: dict[str, SkillDef] = {}
        self._load(content_dir)

    def _load(self, content_dir: Path) -> None:
        def read(name: str) -> list[dict]:
            return json.loads((content_dir / name).read_text(encoding="utf-8"))

        manifest_path = content_dir / _MANIFEST_FILE
        if not manifest_path.is_file():
            raise self._validation_error(
                class_name="*",
                pool="manifest",
                invalid=f"missing key {_MANIFEST_FILE!r}",
                expected="a rules-content manifest",
            )
        try:
            self.manifest = RulesContentManifest.model_validate(
                json.loads(manifest_path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError, PydanticValidationError) as exc:
            raise self._validation_error(
                class_name="*",
                pool="manifest",
                invalid=f"invalid {_MANIFEST_FILE}: {exc}",
                expected="valid backend/UI versions and selectable class keys",
            ) from exc
        self.rules_content_version = self.manifest.backend_rules_content_version
        self.selectable_classes = tuple(self.manifest.selectable_classes)

        for raw in read("classes.json"):
            d = ClassDef.model_validate(raw)
            self.classes[d.name] = d
        for raw in read("subclasses.json"):
            d = SubclassDef.model_validate(raw)
            self.subclasses[d.name] = d
        for raw in read("species.json"):
            d = SpeciesDef.model_validate(raw)
            self.species[d.name] = d
        for raw in read("backgrounds.json"):
            d = BackgroundDef.model_validate(raw)
            self.backgrounds[d.name] = d
        seen_spell_keys: dict[str, str] = {}
        for raw in read("spells.json"):
            d = SpellDef.model_validate(raw)
            canonical = normalize_choice_name(d.name)
            previous = seen_spell_keys.get(canonical)
            if previous is not None:
                raise self._validation_error(
                    class_name="*",
                    pool="spells",
                    invalid=f"duplicate canonical key {d.name!r} (already {previous!r})",
                    expected="every canonical spell key to be unique after normalization",
                )
            seen_spell_keys[canonical] = d.name
            self.spells[d.name] = d
        for raw in read("resources.json"):
            d = ResourceDef.model_validate(raw)
            self.resources[d.definition_id] = d
        for raw in read("skills.json"):
            d = SkillDef.model_validate(raw)
            self.skills[d.name] = d

        self._validate_rules_content()
        self._validate_subclasses()

    def _validate_subclasses(self) -> None:
        seen_keys: set[str] = set()
        seen_ids: set[str] = set()
        for name, subclass in self.subclasses.items():
            if name in seen_keys:
                raise RulesViolation(f"duplicate subclass key: {name}")
            seen_keys.add(name)
            if subclass.definition_id in seen_ids:
                raise RulesViolation(f"duplicate subclass definition_id: {subclass.definition_id}")
            seen_ids.add(subclass.definition_id)
            if subclass.parent_class not in self.classes:
                raise RulesViolation(f"subclass {name} references unknown class {subclass.parent_class!r}")

    def _validation_error(
        self,
        *,
        class_name: str,
        pool: str,
        invalid: str,
        expected: str,
    ) -> RulesViolation:
        return RulesViolation(
            "rules-content validation failed: "
            f"class={class_name}; pool={pool}; invalid={invalid}; "
            f"expected {expected}; rules_content_version={self.rules_content_version}"
        )

    def _spell_choice_names(self, spell: SpellDef) -> tuple[str, ...]:
        return (
            spell.display_name_en,
            spell.name_th_hint,
            *spell.aliases,
        )

    def _validate_rules_content(self) -> None:
        """Reject content that could make the guided build impossible or unsafe."""
        from app.tabletop.rules.core import SUPPORTED_CLASSES

        issues: list[str] = []

        def add_issue(class_name: str, pool: str, invalid: str, expected: str) -> None:
            issues.append(str(self._validation_error(
                class_name=class_name,
                pool=pool,
                invalid=invalid,
                expected=expected,
            )))

        if self.manifest.ruleset_id != RULESET_ID:
            add_issue(
                "*", "manifest", f"ruleset_id {self.manifest.ruleset_id!r}",
                f"ruleset_id {RULESET_ID!r}",
            )
        if self.manifest.ui_rules_content_version != self.rules_content_version:
            add_issue(
                "*", "ui_version",
                f"UI version {self.manifest.ui_rules_content_version!r}",
                f"the backend rules-content version {self.rules_content_version!r}",
            )

        selectable = set(self.selectable_classes)
        if selectable != set(SUPPORTED_CLASSES):
            add_issue(
                "*", "selectable_classes", f"UI classes {sorted(selectable)!r}",
                f"the backend-supported classes {sorted(SUPPORTED_CLASSES)!r}",
            )
        for class_name in self.selectable_classes:
            if class_name not in self.classes:
                add_issue(
                    class_name, "class", f"missing key {class_name!r}",
                    "every selectable class to have a ClassDef",
                )
        # The explicit per-class declaration, the manifest, and the engine must
        # all agree: selectable ⇔ FULLY_SUPPORTED.
        fully = {name for name, cls in self.classes.items()
                 if cls.support_status == "FULLY_SUPPORTED"}
        if fully != selectable:
            add_issue(
                "*", "support_status",
                f"FULLY_SUPPORTED classes {sorted(fully)!r}",
                f"exactly the selectable classes {sorted(selectable)!r}",
            )

        alias_index: dict[str, set[str]] = {}
        feature_names = {normalize_choice_name(name) for name in _KNOWN_CLASS_FEATURE_NAMES}
        for cls in self.classes.values():
            for feature in cls.features:
                feature_names.update(normalize_choice_name(name) for name in (
                    feature.key,
                    feature.key.replace("_", " "),
                    feature.name_th,
                ))

        for spell in self.spells.values():
            for name in (spell.name, *self._spell_choice_names(spell)):
                normalized = normalize_choice_name(name)
                if normalized:
                    alias_index.setdefault(normalized, set()).add(spell.name)

            unknown_classes = sorted(set(spell.classes) - set(self.classes))
            for class_name in unknown_classes:
                add_issue(
                    class_name, "spell.classes", f"spell key {spell.name!r}",
                    "every legal spell class to have class rules content",
                )
            for class_name in sorted(set(spell.classes) & set(self.classes)):
                class_def = self.classes[class_name]
                if (
                    class_def.spellcasting is None
                    or class_def.spellcasting.spell_list != class_name
                ):
                    add_issue(
                        class_name,
                        "spell.classes",
                        f"spell key {spell.name!r}",
                        "every class listed for a spell to have that legal spell list",
                    )
            for class_name in spell.display_classes or []:
                if class_name not in spell.classes:
                    add_issue(
                        class_name, "displayed_spells", f"spell key {spell.name!r}",
                        "every displayed spell to be legal for that class",
                    )

            spell_names = {
                normalize_choice_name(name)
                for name in (spell.name, *self._spell_choice_names(spell))
                if name
            }
            if spell.content_type != "spell" or spell_names.intersection(feature_names):
                class_name = ",".join(spell.classes) or "*"
                add_issue(
                    class_name, f"level_{spell.level}_spells", f"key {spell.name!r}",
                    "spell pools to contain spells only, never class features",
                )

        for alias, keys in sorted(alias_index.items()):
            if len(keys) > 1:
                add_issue(
                    "*", "spell_aliases",
                    f"ambiguous normalized alias {alias!r} -> {sorted(keys)!r}",
                    "every normalized spell name or alias to identify exactly one key",
                )

        for class_name in self.selectable_classes:
            cls = self.classes.get(class_name)
            if cls is None:
                continue

            skill_count = int(cls.skill_choices.get("count", 0))
            skill_config = cls.skill_choices.get("options", [])
            skill_options = (
                list(self.skills) if skill_config == "any" else list(skill_config)
            )
            legal_skills = {key for key in skill_options if key in self.skills}
            if skill_count > len(legal_skills):
                add_issue(
                    class_name, "class_skills",
                    f"required count {skill_count}, legal count {len(legal_skills)}",
                    "the legal skill pool to contain at least the required number of choices",
                )

            if cls.spellcasting is None:
                continue
            casting = cls.spellcasting
            cantrips = self.spells_for_class(casting.spell_list, 0)
            level_one = self.spells_for_class(casting.spell_list, 1)

            if casting.cantrips_known > 0 and not cantrips:
                add_issue(
                    class_name, "cantrips", "legal count 0",
                    f"at least {casting.cantrips_known} legal cantrips",
                )
            elif casting.cantrips_known > len(cantrips):
                add_issue(
                    class_name, "cantrips",
                    f"required count {casting.cantrips_known}, legal count {len(cantrips)}",
                    "the legal pool to contain at least the required number of choices",
                )

            needs_level_one = casting.spellbook_size > 0 or casting.prepared_count > 0
            if needs_level_one and not level_one:
                add_issue(
                    class_name, "prepared_spells", "legal count 0",
                    "a non-empty legal level-1 spell pool",
                )
                continue
            if casting.spellbook_size > len(level_one):
                add_issue(
                    class_name, "spellbook",
                    f"required count {casting.spellbook_size}, legal count {len(level_one)}",
                    "the legal pool to contain at least the required number of choices",
                )
            prepared_source_count = (
                casting.spellbook_size if casting.spellbook_size > 0 else len(level_one)
            )
            if casting.prepared_count > prepared_source_count:
                add_issue(
                    class_name, "prepared_spells",
                    f"required count {casting.prepared_count}, legal count {prepared_source_count}",
                    "the preparation source to contain at least the required number of choices",
                )

        if issues:
            raise RulesViolation("\n".join(issues))

    # --- queries ---------------------------------------------------------------
    def get_class(self, name: str) -> ClassDef:
        d = self.classes.get((name or "").lower())
        if d is None:
            raise RulesViolation(f"unknown class: {name!r}")
        return d

    def get_subclass(self, name: str) -> SubclassDef:
        d = self.subclasses.get((name or "").lower())
        if d is None:
            raise RulesViolation(f"unknown subclass: {name!r}")
        return d

    def subclasses_for_class(self, class_name: str) -> list[SubclassDef]:
        cls = (class_name or "").lower()
        return [s for s in self.subclasses.values() if s.parent_class == cls]

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
        resolution = self.resolve_spell_name(name)
        if resolution.key is not None:
            return self.spells[resolution.key]
        if resolution.ambiguous_keys:
            raise RulesViolation(
                f"ambiguous spell name {name!r}: {list(resolution.ambiguous_keys)!r}"
            )
        raise RulesViolation(f"unknown spell: {name!r}")

    def spell_choice_options(
        self,
        allowed_keys: Iterable[str] | None = None,
    ) -> tuple[ChoiceOption, ...]:
        """Return exact-name options restricted to an authoritative legal pool."""
        if allowed_keys is None:
            keys = list(self.spells)
        else:
            keys = list(dict.fromkeys(allowed_keys))
            unknown = [key for key in keys if key not in self.spells]
            if unknown:
                raise RulesViolation(f"unknown spell keys in legal pool: {unknown!r}")
        return tuple(
            ChoiceOption(key=key, names=self._spell_choice_names(self.spells[key]))
            for key in keys
        )

    def resolve_spell_name(
        self,
        value: str,
        *,
        allowed_keys: Iterable[str] | None = None,
        suggestion_limit: int = 3,
    ) -> ChoiceResolution:
        """Resolve one typed/button spell name without partial matching.

        ``allowed_keys`` must be the current class/step pool.  This keeps a valid
        spell from another class from becoming a legal character-creation pick.
        """
        return resolve_choice_name(
            value,
            self.spell_choice_options(allowed_keys),
            suggestion_limit=suggestion_limit,
        )

    def selectable_class_defs(self) -> list[ClassDef]:
        """Classes the current backend and character-creation UI both support."""
        return [self.classes[key] for key in self.selectable_classes]

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
