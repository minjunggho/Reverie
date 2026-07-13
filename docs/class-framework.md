# Class & rules framework

One authoritative ruleset (**D&D 2024 / SRD 5.2.1** — see `docs/rules-authority.md`)
and one set of reusable systems that every class flows through. There is no
per-class engine code: a class is data (a typed definition) plus the shared systems
that read it.

## The typed class definition (`app/rules_content/registry.py`)

`ClassDef` is the single source of truth for a class:

- key, display names, `hit_die`, `primary_abilities`, `saving_throws`
- armor/weapon training, `base_ac`, skill pool + count, `starting_equipment`
- `subclass_level` + `subclasses` (`SubclassDef`)
- `support_status` (`FULLY_SUPPORTED` selectable / `UNSUPPORTED` locked)
- `spellcasting` (`SpellcastingDef`) with an explicit **casting model**:
  `NONE | KNOWN_SPELLS | PREPARED_SPELLS | SPELLBOOK | PACT_MAGIC | INNATE`,
  the casting ability, cantrip/prepared/spellbook counts, and `slot_resources`
  (spell level → resource id)
- `features` (`FeatureDef`), each with a **level** (when gained) and an
  **activation** (`passive | action | bonus_action | reaction | free | triggered`),
  an optional `resource_id`, `recovery`, and an honest `execution`
  (`supported` = the engine runs it, `narrative` = flavor for now)

Queries: `cls.features_at(level)`, `cls.features_by_activation(a, level)`,
`cls.casting_model`, `reg.class_features_at(name, level)`,
`reg.slot_resource_for(name, spell_level)`.

Startup validation (`RulesViolation` on any incoherence) enforces: the casting
model matches its fields (SPELLBOOK ⇔ spellbook_size>0, etc.); every slot pool /
feature resource references a real resource; feature levels ≥1 and activations
valid; **every spell is resolvable** by the engine; selectable ⇔ FULLY_SUPPORTED ⇔
backend `SUPPORTED_CLASSES`.

## The reusable systems (`app/tabletop/`)

| System | Module | Responsibility |
|---|---|---|
| Derivation | `rules/derive.py` | HP/AC/saves/skills/spell DC, registry-driven, explainable |
| Resources | `resources/engine.py` | grant/spend(atomic, rejects insufficient)/restore + rest recharge, level-scaled, persisted as `ResourceState` |
| Spellcasting | `spellcasting/engine.py` | `spellcasting_profile` + `SpellEngine.cast`: authorize → spend slot → resolve attack/save/damage/heal → concentration → `SPELL_CAST` event |
| Combat | `combat/combat_service.py` | initiative/turns/rounds, action+reaction economy, attack/damage/HP/death, opportunity attacks, combat end |
| Rest | `rest/rest_service.py` | short/long rest, hit dice, resource recharge, 2024 interruption rule |
| Concentration | `effects/concentration.py` | one effect at a time, CON save on damage, incapacitation ends it |
| Capabilities | `progression/capabilities.py` | composes features+resources+spellcasting for one character (sheet/action surfaces) |
| Level-up | `progression/level_up.py` | level+1: HP (fixed average), proficiency, new features, re-scaled resources |

The spell engine reads the **same registry** character creation selects from, so a
spell can't be selectable-but-unresolvable — startup validation guarantees every
spell has a concrete effect (attack/save/damage/healing/utility) the engine runs.

## Fixtures & coverage (`tests/test_class_framework.py`, 16 tests)

Archetypes exercised through the shared systems: martial (fighter — Second Wind /
Action Surge resources, level-2 feature reveal), prepared caster (cleric — DC,
heal, concentration), known caster (bard model), spellbook caster (wizard — slot
spend/reject, attack cantrip, save-half), pact caster (warlock — PACT_MAGIC model +
pact slots, framework-level), resource class (barbarian — level-scaled Rage,
framework-level). Plus: resource atomicity + restart persistence, capabilities
composition, level-up scaling, and the **unlock discipline** (all twelve core
classes are FULLY_SUPPORTED and selectable; a class outside the SRD content —
Artificer — is still rejected by `validate_class`).

## Caster mechanics (Phase 4A) — `app/tabletop/classes/`

Class-specific kit built ON the shared systems (no parallel resource/spell/
persistence code), with tests in `tests/test_caster_classes.py`:

- **Bard** (`bard.py`): Bardic Inspiration die scaling (d6→d12), Jack of All Trades
  (folded into `derive.skill_bonus`, guarded to bard L2+), Song of Rest die. Bardic
  Inspiration is a ResourceState pool.
- **Sorcerer** (`sorcerer.py`): Sorcery Points (level-scaled, from L2), Font of
  Magic slot⇄point conversion (SRD table, atomic, rejects insufficient), Metamagic
  catalog + `apply_metamagic` (spends SP, returns an effect descriptor).
- **Warlock** (`warlock.py`): Pact slots (short-rest recharge via the shared rest
  engine), Eldritch Invocations catalog with typed prerequisites (level / known
  cantrip / pact), Pact Boon grants.
- **Wizard** (`wizard.py`): spellbook `learn`/`prepare`, ritual casting (slot-free
  path in `SpellEngine.cast(ritual=True)`), Arcane Recovery (resource + rest
  special recharge).

`finalize.py` now grants slot pools from the class's declared `slot_resources`
(warlock → pact slots, arcane casters → the arcane pool) — the earlier hardcoded
`spell_slots_1` is gone — and only grants features/resources at the character's
level.

## What is done, per class

**All twelve core classes are fully supported & selectable.** Creation, finalize,
derived stats, resources, spell selection + honest cast, class-feature activation,
combat integration, rest recovery, restart persistence, level + subclass
progression, and sheet all work through the shared systems — no per-class engine.
The remaining unsupported class (Artificer) has no SRD content and falls back to a
chassis at creation; `validate_class` rejects it loudly.

## Framework gaps closed (Step 5) — spell cast path + subclass progression

Both framework-wide gaps that blocked the caster unlock are now shipped:

- **Real spellcasting in committed actions.** `! ร่าย <spell> ใส่ <target>` →
  `ActionInterpretation.cast_intent` → `CommittedActionPipeline._handle_cast` →
  the authoritative spell resolver (against the caster's OWN pool) → target +
  stats from the scene/`Combatant`/`Character` (never the LLM) → `SpellEngine.cast`
  → atomic commit (slot/damage/healing/concentration/`SPELL_CAST` event) →
  narration from the committed result. Invalid casts consume nothing; duplicate
  Discord deliveries cast once (existing `ProcessedMessage` dedup). Damage lands on
  authoritative `Combatant` HP in combat; healing/buffs on `Character`; an
  attack/save spell with no authoritative target stats fails safe with a Thai
  diagnostic. Tested in `tests/test_spell_pipeline.py` (Thai/English/alias forms,
  stored AC/save, damage/heal/concentration/slot-once/invalid/duplicate/restart,
  per selectable caster, + a ReverieClient smoke test).
- **Subclass progression.** `Character.active_subclass` (migration
  `20260715_subclass`) is distinct from the narrative `planned_subclass`.
  `SubclassService` validates (parent class + selection level), persists the active
  subclass, and grants its features/resources/always-prepared spells through the
  shared systems (idempotent). `level_up` reads each class's own `subclass_level`
  and **pauses** (`SubclassSelectionRequired`) until a valid choice is confirmed —
  a plan never auto-activates. Tested in `tests/test_subclass_progression.py`.

## The unlock: all 12 core classes selectable

Every unlock cleared the same bar — `FULLY_SUPPORTED` + selectable **only when the
full end-to-end acceptance path passes** (creation → feature/cast through the
committed pipeline → resources → combat → rest → restart → level → subclass), never
by editing the selectable list alone (startup validation forbids it).

- **Sorcerer, Warlock** (`tests/test_unlock_sorcerer_warlock.py`): guided finalize
  with the class-declared slot pool, then a real cast committing damage to a
  combatant.
- **Fighter, Rogue, Barbarian, Monk** (`tests/test_martial_classes.py`, 15):
  Fighter (Second Wind + Action Surge through the feature-activation pipeline, Extra
  Attack L5, Indomitable L9), Rogue (Sneak Attack with *validated eligibility* —
  finesse/ranged + advantage-or-ally + not disadvantage, never vibes; Cunning Action
  L2, Uncanny Dodge L5, Evasion L7), Barbarian (Rage as a non-concentration
  ActiveEffect with physical resistance + a level-scaled damage bonus applied in
  combat, Reckless Attack, Danger Sense, Extra Attack L5, Unarmored Defense = CON),
  Monk (Martial Arts die, Ki/Focus abilities spending Focus with short-rest
  recovery, Unarmored Defense = WIS, Stunning Strike L5).
- **Cleric, Ranger, Druid, Paladin** (`tests/test_divine_nature_classes.py`, 16):
  Cleric (Channel Divinity through the activation pipeline), Ranger (Hunter's Mark
  cast through the SpellEngine as a concentration buff, spellcasting from L1),
  Druid (**Wild Shape from authoritative `BeastFormDef` content** — CR-gated legal
  forms by druid level, form HP carried as a temp pool so the base sheet is never
  corrupted, clean revert, use recovers on short rest; the LLM never invents a
  form), Paladin (**Lay on Hands as a class feature, never a spell** — a 5×level HP
  pool drawn through `ClassFeatureService`, asserted absent from every spell pool;
  Divine Smite spends a spell slot for radiant damage; half-caster spellcasting
  granted at L2, guarded in `finalize`).

**Shared primitive:** `_handle_activate` + `ClassFeatureService`
(`app/tabletop/classes/features.py`) — "ใช้ <feature>" spends the feature's
resource via ResourceEngine and applies its committed effect; combat-integrated
pieces (Rage, Sneak Attack, Extra Attack, rage resistance) live in
`martial_combat.py`, and Wild Shape in `druid.py` (`WildShapeService`). Channel
Divinity, Divine Smite, Lay on Hands, and Wild Shape all reuse this one activation
verb — the LLM decides neither availability nor numbers.

Selectable classes are now all twelve: fighter, rogue, wizard, cleric, ranger,
bard, sorcerer, warlock, barbarian, monk, druid, paladin. The old prepared-spell
creation deadlock stays permanently covered by a regression test in
`test_divine_nature_classes.py`.

## Known integration gaps (framework built, wiring pending)

- **Higher-level spell slots:** `slot_resources` is keyed by spell level and the
  engine spends by level; content currently ships 1st-level slots (correct for
  level-1 play). Higher slot pools are additive content, no new code.
- **Prepared-spell changes on long rest** are modeled (the rest opens
  re-preparation); the preparation-change UI is a creation/level-up follow-up.
