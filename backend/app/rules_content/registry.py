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
# The one authoritative edition (see docs/rules-authority.md). Surfaced by
# diagnostics and stamped in the manifest so a deployment is provably on it.
RULESET_EDITION = "D&D 2024 (SRD 5.2.1)"
_CONTENT_DIR = Path(__file__).parent / "srd_5_2_1"
_MANIFEST_FILE = "manifest.json"

# The spellcasting models a class definition may declare. One authoritative set,
# so the class model — not scattered per-class code — says HOW a class casts.
SPELLCASTING_MODELS = (
    "NONE", "KNOWN_SPELLS", "PREPARED_SPELLS", "SPELLBOOK", "PACT_MAGIC", "INNATE",
)

# Feature activation types the execution framework understands.
FEATURE_ACTIVATIONS = (
    "passive", "action", "bonus_action", "reaction", "free", "triggered",
)

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
    healing: str | None = None     # e.g. "1d8"
    ux_category: str               # โจมตี/ป้องกัน/ควบคุม/สำรวจ/ภาพลวงตา/ฟื้นฟู/ใช้งาน
    mech_summary_th: str
    classes: list[str] = Field(default_factory=list)
    # HOW the spell resolves — explicit so the engine never guesses:
    #   attack: the target is hit by a spell attack roll (none|melee|ranged).
    #   save_ability: the target rolls a save vs the caster's DC (str..cha), else None.
    #   save_effect: brief Thai description of what a failed save does.
    #   half_on_save: damage is halved on a successful save (default for save spells).
    attack: Literal["none", "melee", "ranged"] = "none"
    save_ability: str | None = None
    save_effect: str = ""
    half_on_save: bool = False
    scales_with_slot: bool = False   # extra damage/effect when cast at a higher slot
    # Optional explicit UI declaration.  When present it must be a subset of the
    # authoritative legal ``classes`` list or startup validation fails.
    display_classes: list[str] | None = None
    content_type: Literal["spell", "class_feature"] = "spell"

    @property
    def display_name_en(self) -> str:
        return self.name_en or self.name.replace("_", " ").title()

    @property
    def is_cantrip(self) -> bool:
        return self.level == 0


class RulesContentManifest(BaseModel):
    ruleset_id: str
    backend_rules_content_version: str
    ui_rules_content_version: str
    selectable_classes: list[str]
    # The authoritative edition string (see docs/rules-authority.md). Optional for
    # back-compat; when present it must match RULESET_EDITION.
    rules_edition: str = RULESET_EDITION


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
    # The authoritative casting model for the class (see SPELLCASTING_MODELS).
    #   KNOWN_SPELLS    - a fixed list learned (bard, ranger-2024 knows+prepares)
    #   PREPARED_SPELLS - prepares from the whole class list each day (cleric)
    #   SPELLBOOK       - a spellbook + daily preparation (wizard)
    #   PACT_MAGIC      - few slots, all at the highest level, short-rest recharge (warlock)
    #   INNATE          - fixed at-will/per-day spells (some species/monsters)
    model: Literal[
        "NONE", "KNOWN_SPELLS", "PREPARED_SPELLS", "SPELLBOOK", "PACT_MAGIC", "INNATE"
    ] = "PREPARED_SPELLS"
    ability: str
    cantrips_known: int = 0
    spellbook_size: int = 0                # wizard only
    prepared_count: int = 0
    spell_list: str = ""                   # class key used to filter SpellDef.classes
    # Slot pool resource id keyed by SPELL level (1 = 1st-level slots). Level-1
    # characters only ever use 1st-level slots; higher entries extend the same
    # atomic ResourceEngine path without new code.
    slot_resources: dict[str, str] = Field(
        default_factory=lambda: {"1": "resource:spell_slots_1"})


class BaseAC(BaseModel):
    # unarmored_con = Barbarian Unarmored Defense (10 + DEX + CON);
    # unarmored_wis = Monk Unarmored Defense (10 + DEX + WIS).
    kind: Literal["unarmored", "flat", "light", "medium", "unarmored_con", "unarmored_wis"]
    value: int = 10          # flat AC, or armor base for light/medium
    dex_cap: int | None = None
    shield: bool = False


class FeatureDef(BaseModel):
    key: str
    name_th: str
    summary_th: str
    # WHEN the feature is gained and HOW it is used — so level progression and the
    # action economy are represented in data, not scattered in code.
    level: int = 1                          # minimum class level to have it
    activation: Literal[
        "passive", "action", "bonus_action", "reaction", "free", "triggered"
    ] = "passive"
    resource_id: str | None = None          # limited-use pool this feature spends
    recovery: str = ""                       # short_rest | long_rest | "" (see resource)
    display_th: str = ""                     # optional player-facing one-liner
    # Whether the engine can mechanically EXECUTE this feature today, vs. it being
    # narrative/flavor for now. Honest, like the class support levels.
    execution: Literal["supported", "narrative"] = "narrative"
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
    subclass_level: int = 3                # class level at which a subclass is chosen
    starting_equipment: list[str] = Field(default_factory=list)  # class kit (2024)
    features: list[FeatureDef] = Field(default_factory=list)
    spellcasting: SpellcastingDef | None = None
    concept_keywords: list[str] = Field(default_factory=list)  # Thai recommend hints

    @property
    def casting_model(self) -> str:
        return self.spellcasting.model if self.spellcasting else "NONE"

    def features_at(self, level: int) -> list[FeatureDef]:
        """Every class feature a character of this class has AT `level` — the one
        authoritative answer to 'what does a level-N X have', used by the sheet,
        level-up, and the action-economy view."""
        return [f for f in self.features if f.level <= level]

    def features_by_activation(self, activation: str, level: int) -> list[FeatureDef]:
        return [f for f in self.features_at(level) if f.activation == activation]


class SubclassFeatureDef(BaseModel):
    key: str
    name_th: str
    summary_th: str = ""
    level: int = 3                          # level at which this subclass feature lands
    activation: Literal[
        "passive", "action", "bonus_action", "reaction", "free", "triggered"
    ] = "passive"
    resource_id: str | None = None          # a limited-use pool the subclass grants
    grants_spells: list[str] = Field(default_factory=list)  # always-prepared subclass spells


class SubclassDef(_Def):
    name: str
    name_th: str
    pitch_th: str
    parent_class: str
    selection_level: int = 3
    concept_keywords: list[str] = Field(default_factory=list)
    features: list[SubclassFeatureDef] = Field(default_factory=list)
    implementation_status: str = "planned"

    def features_at(self, level: int) -> list[SubclassFeatureDef]:
        return [f for f in self.features if f.level <= level]


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
        # One authoritative edition — the manifest may not claim a different one.
        if self.manifest.rules_edition != RULESET_EDITION:
            add_issue(
                "*", "rules_edition", f"edition {self.manifest.rules_edition!r}",
                f"the authoritative edition {RULESET_EDITION!r}",
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

        # --- framework coherence: casting model, features, spell resolution ------
        self._validate_framework(add_issue)

        if issues:
            raise RulesViolation("\n".join(issues))

    def _validate_framework(self, add_issue) -> None:
        """The class/feature/spell TYPES must be internally coherent — the model
        declares HOW each class casts, WHEN each feature is gained, and HOW each
        spell resolves, and none of it may contradict itself or reference missing
        resources. Applies to ALL classes in the pack, not just selectable ones,
        so locked classes stay honestly represented."""
        for name, cls in self.classes.items():
            sc = cls.spellcasting
            model = cls.casting_model
            if model not in SPELLCASTING_MODELS:
                add_issue(name, "casting_model", f"model {model!r}",
                          f"one of {list(SPELLCASTING_MODELS)}")
            # Model ⇔ fields coherence.
            if sc is not None:
                if model == "SPELLBOOK" and sc.spellbook_size <= 0:
                    add_issue(name, "casting_model", "SPELLBOOK with spellbook_size=0",
                              "a spellbook model to have spellbook_size > 0")
                if model in ("PREPARED_SPELLS", "KNOWN_SPELLS") and sc.spellbook_size > 0:
                    add_issue(name, "casting_model", f"{model} with a spellbook",
                              "only the SPELLBOOK model to declare spellbook_size")
                for lvl, rid in sc.slot_resources.items():
                    if rid not in self.resources:
                        add_issue(name, "slot_resources", f"missing resource {rid!r}",
                                  "every slot pool to reference a real resource definition")
            # Features: valid level + activation + resource refs; supported features
            # must actually have something to spend or execute.
            for feat in cls.features:
                if feat.level < 1:
                    add_issue(name, "feature_level", f"{feat.key} level {feat.level}",
                              "every feature level to be >= 1")
                if feat.activation not in FEATURE_ACTIVATIONS:
                    add_issue(name, "feature_activation",
                              f"{feat.key} activation {feat.activation!r}",
                              f"one of {list(FEATURE_ACTIVATIONS)}")
                if feat.resource_id and feat.resource_id not in self.resources:
                    add_issue(name, "feature_resource",
                              f"{feat.key} -> {feat.resource_id!r}",
                              "every feature resource to reference a real definition")

        # Every spell must be RESOLVABLE by the engine (honest selection): a cantrip
        # or a leveled spell that has at least one concrete effect the engine runs.
        for spell in self.spells.values():
            if spell.content_type != "spell":
                continue
            resolvable = bool(spell.damage or spell.healing or spell.attack != "none"
                              or spell.save_ability or spell.ux_category)
            if not resolvable:
                add_issue(",".join(spell.classes) or "*", "spell_resolution",
                          f"spell {spell.name!r} has no resolvable effect",
                          "every spell to have damage/healing/attack/save/utility")
            if spell.save_ability and spell.save_ability.lower() not in (
                    "str", "dex", "con", "int", "wis", "cha"):
                add_issue(",".join(spell.classes) or "*", "spell_save",
                          f"spell {spell.name!r} save {spell.save_ability!r}",
                          "a valid saving-throw ability")

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

    def class_features_at(self, class_name: str, level: int) -> list[FeatureDef]:
        """Framework query: the class features a level-`level` character of
        `class_name` has. One path for the sheet, level-up, and action economy."""
        return self.get_class(class_name).features_at(level)

    def slot_resource_for(self, class_name: str, spell_level: int) -> str | None:
        """The ResourceState id backing spell slots of `spell_level` for this class,
        or None if the class has no such slots (or isn't a caster)."""
        cls = self.get_class(class_name)
        if cls.spellcasting is None:
            return None
        return cls.spellcasting.slot_resources.get(str(spell_level))

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
