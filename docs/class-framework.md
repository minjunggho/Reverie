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
composition, level-up scaling, and the **locked-class discipline** (barbarian/
warlock/sorcerer/paladin/druid/monk are represented in the framework but not
selectable, and creation rejects them).

## What is done vs. remaining, per class

**Fully supported & selectable (6):** fighter, rogue, wizard, cleric, ranger, bard.
Creation, finalize, derived stats, resources, spell selection + honest cast, rest,
restart persistence, and sheet all work through the shared systems.

**Locked — represented in the framework, not yet playable:**

| Class | Model piece present | Remaining before unlock |
|---|---|---|
| barbarian | Rage resource (level-scaled), Unarmored Defense | Rage damage/resistance execution in combat; class-specific tests |
| monk | Focus/Ki resource, Martial Arts feature | unarmed strike + ki-fueled actions execution; tests |
| paladin | Lay on Hands + Channel Divinity resources, PREPARED model | smite/aura execution; spell list content; tests |
| sorcerer | KNOWN model | Sorcery Points + Metamagic; spell list content; tests |
| warlock | PACT_MAGIC model + pact slots | Pact Boon / invocations; short-rest slot recharge wiring; spell list; tests |
| druid | PREPARED model | Wild Shape statblocks; nature spell list; tests |

Unlock criterion (unchanged from the mandate): a class becomes `FULLY_SUPPORTED`
and selectable **only when its class-specific mechanics are implemented and its own
tests pass** — never by editing the selectable list alone (startup validation
forbids that).

## Known integration gaps (framework built, wiring pending)

- **Discord cast path:** `SpellEngine.cast` is the honest core and is tested
  directly; routing a natural-language `! ร่ายไฟลูกไฟใส่...` through the committed
  pipeline to the engine (target/AC resolution from the scene) is the next
  integration.
- **Higher-level spell slots:** `slot_resources` is keyed by spell level and the
  engine spends by level; content currently ships 1st-level slots (correct for
  level-1 play). Higher slot pools are additive content, no new code.
- **Prepared-spell changes on long rest** are modeled (the rest opens
  re-preparation); the preparation-change UI is a creation/level-up follow-up.
