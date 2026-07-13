# Post-Step-4 handoff

**Branch:** `reverie/phase-3-class-framework` · **Commit:** `25e38c0` (this audit adds
one doc-only commit on top, not pushed/merged) · **Migration head:**
`20260715_subclass` · **Tests:** 359 passed · compileall clean · `git diff --check`
clean · `alembic upgrade head` verified fresh.

This document corrects one overclaim from the Step 4 report — **"subclass
selection works" was true at the code/service level but not fully wired into
player-facing surfaces** — and gives the exact next-phase plan. No code changed in
this pass except this document; the audit below is read-only findings.

## Corrected class-support matrix

Labels: `COMPLETE` · `PARTIAL` · `IMPLEMENTED_UNVERIFIED` · `NOT_IMPLEMENTED`.
No selectable class's label changed to worse-than-selectable — the finding is that
**subclass depth and level-up delivery were narrower than "full support" implies**,
which the original Step 4 report did not claim was complete anyway (it said
"selectable classes... work through the shared systems," not "every subclass
works" or "level-up is playable").

| Class | Creation | Core mechanics | Spellcasting | Subclass *selection* | Subclass *coverage* (of 4) | Level progression (service) | Level progression (Discord) | Discord cast path | Gateway-verified |
|---|---|---|---|---|---|---|---|---|---|
| Fighter | COMPLETE | COMPLETE | n/a | COMPLETE | PARTIAL (1/4: champion) | COMPLETE | **NOT_IMPLEMENTED** | n/a | NOT_IMPLEMENTED |
| Rogue | COMPLETE | COMPLETE | n/a | COMPLETE | PARTIAL (1/4: thief) | COMPLETE | NOT_IMPLEMENTED | n/a | NOT_IMPLEMENTED |
| Wizard | COMPLETE | COMPLETE | COMPLETE | COMPLETE | PARTIAL (1/4: evoker) | COMPLETE | NOT_IMPLEMENTED | IMPLEMENTED_UNVERIFIED | NOT_IMPLEMENTED |
| Cleric | COMPLETE | COMPLETE | COMPLETE | COMPLETE | PARTIAL (1/4: life_domain) | COMPLETE | NOT_IMPLEMENTED | IMPLEMENTED_UNVERIFIED | NOT_IMPLEMENTED |
| Ranger | COMPLETE | COMPLETE | COMPLETE | COMPLETE | PARTIAL (1/4: gloom_stalker) | COMPLETE | NOT_IMPLEMENTED | IMPLEMENTED_UNVERIFIED | NOT_IMPLEMENTED |
| Bard | COMPLETE | COMPLETE | COMPLETE | COMPLETE | PARTIAL (1/4: college_of_lore) | COMPLETE | NOT_IMPLEMENTED | IMPLEMENTED_UNVERIFIED | NOT_IMPLEMENTED |
| Sorcerer | COMPLETE | COMPLETE (SP/Metamagic) | COMPLETE | COMPLETE (validates+persists) | **NOT_IMPLEMENTED (0/4 — all four Origins are feature-less stubs)** | COMPLETE | NOT_IMPLEMENTED | IMPLEMENTED_UNVERIFIED | NOT_IMPLEMENTED |
| Warlock | COMPLETE | COMPLETE (Invocations/Pact Boon) | COMPLETE | COMPLETE (validates+persists) | **NOT_IMPLEMENTED (0/4 — all four Patrons are feature-less stubs)** | COMPLETE | NOT_IMPLEMENTED | IMPLEMENTED_UNVERIFIED | NOT_IMPLEMENTED |

**Key findings, precise:**

1. **Subclass coverage is 1-in-4 (or 0-in-4) everywhere, not 4-in-4.** Every class's
   subclass list has exactly one entry with real `features` (`SubclassFeatureDef`
   with level/activation/resource/spells); the other three have `features: []` and
   `implementation_status: "planned"`. Selecting one of the three stubs is not
   rejected — it succeeds and grants zero mechanical features, silently. That is
   not a data-integrity bug (nothing invents a power), but it does mean "pick your
   subclass" is misleadingly presented as 4 real choices when only 1 is mechanical.
2. **Sorcerer and Warlock have ZERO mechanical subclasses.** `select_subclass`
   validates and persists correctly, but every one of the 4 Origins/Patrons is a
   stub — so subclass selection for these two classes currently always grants
   nothing. This does not violate the unlock gate (the gate tested class-level
   mechanics + casting, not subclass depth), but it should be visible to anyone
   deciding whether these classes are "done."
3. **Level-up has NO Discord entry point.** `app/tabletop/progression/level_up.py`
   is fully tested at the service layer but is not called from `admin_bridge.py` or
   anywhere in the Discord-facing code — there is no `!rv level up`, no XP
   accumulation, no automatic trigger. A character created at level 1 today has no
   in-product way to reach level 2, so `SubclassSelectionRequired` has never fired
   in a live flow. This is the single biggest gap between "level progression works"
   (true, as a library) and "you can level up your character" (false, today).
4. **The sheet does not show subclass.** `app/services/views.py`'s
   `build_character_sheet` has no reference to `active_subclass`, `planned_subclass`,
   or `SubclassService.subclass_features()`. The data persists correctly (proven in
   `test_subclass_progression.py`) but a player cannot see their subclass on `!rv
   sheet` today.
5. **No class's Discord cast path has been run through a real Discord gateway.**
   Every "IMPLEMENTED_UNVERIFIED" cell above means: proven through the real
   `ReverieClient._route`/`_deliver` callback (no token/guild), never through an
   actual bot connection. This has been the standing limitation since Phase 1 of
   this engagement and remains unchanged.

No labels for the eight *selectable* classes' base mechanics/casting were weakened
— those remain accurately COMPLETE at the layer tested (creation → cast → resource
→ rest → restart, all proven end-to-end). The correction is scoped to subclass
depth and level-up delivery, which were never claimed complete in the Step 4
report's own matrix (it listed "subclass selection" as a column, not "all
subclasses" or "level-up is playable").

## Known spell-engine limitations (tickets, not implemented)

### A. Area-of-effect spells apply only to the primary target

**Current behavior:** `SpellEngine.cast` takes `target_acs`/`target_save_mods` as
dicts, but the pipeline (`_handle_cast`) only ever resolves and passes ONE target
(`(npc_targets or pc_targets or [None])[0]`). A spell like Burning Hands narratively
implies "everyone in the area" but mechanically only hits the one resolved target.

**Required implementation (design, not built):**

1. **Area shape** on `SpellDef`: add `area_shape: Literal["none","cone","line","sphere","cube"]`
   and `area_size: int` (feet), populated from real SRD data per spell.
2. **Origin point**: for a cone/line, the origin is the caster's position; for a
   sphere/cube "at a point," the player names a point (a location feature or a
   named creature's position as a proxy — no coordinate grid exists in this engine
   today, so origin resolution needs a design decision: reuse the Scene's
   `visible_entity_ids` as the eligible-target universe rather than true geometry).
3. **Eligible targets**: everyone in the scene who is a plausible occupant of the
   area — since there is no grid, the pragmatic v1 is "all hostiles in the scene"
   for an attack area, with an explicit **friendly-fire opt-in** the caster must
   state ("รวมพวกเราด้วย") — never silently include allies.
4. **Per-target resolution loop**: for each eligible target, run the SAME
   attack-or-save logic `cast()` already has, once per target, collecting a list of
   `SpellCastOutcome`-like per-target records instead of one.
5. **Half damage / resistance / immunity**: `half_on_save` already exists; add
   `Character`/`Combatant` `resistances`/`immunities` fields (none exist today —
   `resistances()` in `derive.py` only reads species-trait resistances) and apply
   them per target before committing HP change.
6. **Atomicity**: the WHOLE per-target loop must be one unit-of-work — a slot is
   spent once regardless of target count; if the transaction fails partway, nothing
   commits (already true structurally since `cast()` runs inside the caller's
   transaction — the loop must stay inside that same transaction, not open new ones
   per target).
7. **Events**: one `SPELL_CAST` event with a `targets` list (already supported by
   `EventService.record(target_entities=[...])`) plus per-target `DAMAGE_APPLIED`
   events (reuse the existing event type, do not invent a new one).
8. **Narration**: the pipeline's narration step needs a multi-target line builder —
   `_cast_line` in `engine.py` currently assumes one target; extend it to summarize
   N outcomes ("3 คนโดน 2 คนหลบ") without inventing which specific NPCs were hit
   beyond what the per-target loop actually resolved.
9. **Duplicate-delivery safety**: unchanged — the existing `ProcessedMessage` dedup
   already covers the whole committed action regardless of target count; no new
   idempotency mechanism needed.

**Estimated size:** medium — touches `SpellDef`, `SpellEngine.cast` (loop
refactor), `_handle_cast` (target-set resolution), `_cast_line`, and needs
`resistances`/`immunities` fields on `Character`/`Combatant` (new migration).
**Claude-architecture-review required**: yes — the "no grid" origin/eligibility
design decision (item 2/3) is a real architectural call, not a mechanical
implementation detail.

### B. Save-based spells outside active combat

**Current behavior:** `_target_combat_stats` in `pipeline.py` returns AC/save
modifiers only from a `Combatant` row (requires active combat) or a `Character`
row (a PC). An NPC targeted with a save spell *outside combat* has no authoritative
save modifiers anywhere — the pipeline currently fails safe with a Thai diagnostic
("ยังไม่มีค่ากลไกของเป้าหมาย...") rather than inventing one. That's correct behavior
for now, but it makes non-combat social/exploration spellcasting against NPCs
effectively unusable.

**Options evaluated:**

| Option | Pros | Cons |
|---|---|---|
| Persistent NPC mechanical-stat profile (new columns/table on `NPC`) | Simple, always available, no combat needed | Every NPC needs stats authored even if never fought; scope creep for narrative-only NPCs |
| Auto-generate an Encounter snapshot from an "approved stat block" the instant a mechanical action targets an NPC | Reuses `Combatant` fully (zero new persistence) | Needs a stat-block SOURCE — where do AC/saves come from for an NPC that was never meant to fight? Still requires authored data somewhere |
| Temporary `Combatant` created ad hoc (not inside a `CombatEncounter`) | Reuses the Combatant row/columns | `Combatant.encounter_id` is NOT NULL FK — would need a schema change or a "ambient" encounter-less combatant, breaking the existing invariant that Combatants belong to an Encounter |
| Scene-entity mechanical profile (attach optional AC/saves to the Scene's `visible_entity_ids` metadata) | Scoped to what's actually present, no permanent NPC bloat | New per-scene data model; duplicates what Combatant already does structurally |
| **Campaign-defined NPC stat blocks (RECOMMENDED)** | One authoritative source, reusable in AND out of combat, matches how `ClassDef`/`SpellDef` already work (typed content, not ad hoc) | Requires authoring effort per NPC that needs to be mechanically targetable |

**Recommendation: campaign-defined NPC stat blocks**, added as an optional
mechanical-profile block on the `NPC` model (or a linked `NPCStatBlock` row —
1:1, nullable) with `ac`, `save_bonuses: dict[str,int]`, `hp`, `max_hp`,
`resistances`, `immunities`. Rationale:

- It is the SAME pattern as everything else in this codebase: mechanical truth is
  typed, authored, and validated — never invented at cast time. `ClassDef`,
  `SpellDef`, `SubclassDef` are all "someone authored this, the engine reads it";
  an `NPCStatBlock` is consistent with that architecture, whereas a temporary
  Combatant or scene-attached profile would be a parallel, one-off mechanism.
- It composes with the EXISTING `Combatant` system rather than replacing it:
  `CombatService.start_combat` can read `NPCStatBlock` (when present) to seed
  `CombatantSpec` instead of requiring the caller to invent numbers, so combat
  entry gets easier too, not just non-combat casting.
- An NPC with no stat block simply cannot be mechanically targeted — which is the
  CORRECT fail-safe (an ambient tavern-keeper should not have secret AC/saves
  invented the moment a player casts Charm Person at them; the owner/DM must
  decide this NPC is mechanically real by authoring the block, exactly like
  choosing to start combat today).

**Not implementing now** — this is a design recommendation only, per instruction.

## Four locked classes — implementation plan (two phases, not started)

Every ticket below explicitly reuses: `ResourceEngine` (`app/tabletop/resources/`),
`SpellEngine`/`spellcasting_profile` (`app/tabletop/spellcasting/`),
`SubclassService`/`level_up` (`app/tabletop/progression/`), `derive.py` bonuses,
`ConcentrationService` (`app/tabletop/effects/`), `CombatService`
(`app/tabletop/combat/`), `RestService` (`app/tabletop/rest/`), the `FeatureDef`/
`ResourceDef` typed content model, and `CharacterGrant` for all persistence. **No
new resource, spell, feature, persistence, or progression system may be created —
if a ticket seems to need one, that's a signal to stop and get Claude review.**

### Phase A — Barbarian, Monk

| Ticket | Reuses | Files | Codex-safe? |
|---|---|---|---|
| A1. Barbarian creation + starting equipment | `build_flow.py` class-step pattern (already generic), `presets.py` | `presets.py`, `classes.json` (starting_equipment) | **Codex-safe** (pure content + following the existing pattern) |
| A2. Rage resource activation (bonus action, sets a flag/ActiveEffect) | `ResourceEngine.spend`, a NEW `ActiveEffect`-style row is arguably needed since Rage is a STANCE not a one-shot spend — **this is the one item needing Claude review**: decide whether Rage state reuses `ActiveEffect` (currently concentration-only) or needs a `character.conditions` list entry | `app/tabletop/classes/barbarian.py` (new, mirrors `sorcerer.py`/`warlock.py` pattern) | Claude review required for the ActiveEffect-vs-conditions decision; Codex can implement after that decision is made |
| A3. Rage ending conditions (no attack/no damage taken for a round, or voluntary) | Needs a turn-boundary hook in `CombatService.end_turn` | `combat/combat_service.py` | Claude review (touches shared CombatService) |
| A4. Rage damage bonus + resistance (physical) | `DiceEngine.resolve_damage` flat_modifier param already supports a bonus; resistance needs the SAME `resistances`/`immunities` field the AoE ticket also needs (share the migration) | `combat_service.py`, `derive.py` | Codex-safe once the resistance field exists |
| A5. Unarmored Defense (CON to AC) | `derive.armor_class` already branches on `base_ac.kind` — add an `"unarmored_con"` kind | `registry.py` (BaseAC kind enum), `derive.py` | Codex-safe |
| A6. Reckless Attack (advantage on attacks, advantage on attacks against you) | `DiceEngine.resolve_attack(advantage=...)` already exists | combat resolution call sites | Codex-safe |
| A7. Danger Sense (advantage on DEX saves) | `save_bonus`/save resolution | `derive.py` | Codex-safe |
| A8. Rest recovery (Rage uses per long rest) | `ResourceEngine.apply_long_rest` — ALREADY generic, just needs the resource definition | `resources.json` | Codex-safe |
| A9. Level progression (features_at already generic) | `level_up.py` — no change needed, just content | content only | Codex-safe |
| A10. Barbarian subclasses (Path features) | `SubclassFeatureDef` — same pattern as champion/thief/etc. | `subclasses.json` | Codex-safe (follow the existing 1-real-subclass pattern, or better: do all 4 this time) |
| A11. Discord actions (`! เข้าสู่ความเกรี้ยวกราด` → Rage) | Needs `cast_intent`-style NEW interpreter intent (`rage_intent`?) OR reuse `social_intent`-adjacent free-feature activation path — **Claude review required**: this is a new pipeline branch, same shape as `_handle_cast`/`_handle_rest` | `pipeline.py`, `llm_io.py` | Claude review required |
| A12. Persistence + restart tests | existing patterns from `test_subclass_progression.py`/`test_caster_classes.py` | `test_barbarian.py` (new) | Codex-safe (mechanical, follows existing test patterns exactly) |
| A13. End-to-end test (creation→rage→combat→rest→restart) | `test_unlock_sorcerer_warlock.py` as the template | `test_unlock_barbarian_monk.py` | Codex-safe once A2/A3/A11 architecture decisions are made by Claude |

Monk ticket table (same reuse discipline):

| Ticket | Reuses | Codex-safe? |
|---|---|---|
| M1. Creation + equipment | same as A1 | Codex-safe |
| M2. Martial Arts (unarmed die scales, DEX-for-attack option) | `derive.py` damage-die selection — needs a new small helper, same shape as `bard.py`'s die-scaling functions | Codex-safe (clear precedent) |
| M3. Unarmored Defense (DEX+WIS) | same `BaseAC` extension as A5 (different ability pair) | Codex-safe |
| M4. Focus/Ki resource | `resource:focus` ALREADY EXISTS in content (added in Phase 4A framework-proofing) — just wire the feature | Codex-safe |
| M5. Flurry of Blows / Patient Defense / Step of the Wind (Ki-spent bonus actions) | `ResourceEngine.spend` on `resource:focus`, same pattern as Metamagic's `apply_metamagic` | Codex-safe |
| M6. Unarmored Movement (speed bonus) | `Character.speed` direct modifier at derive-time | Codex-safe |
| M7. Deflect Missiles | needs a reaction-trigger hook — **Claude review** (new reaction path, similar concern to A3) | Claude review |
| M8. Stunning Strike at the correct level | `Character.conditions` list (already exists) + `features_at(level)` gate | Codex-safe |
| M9-M13. Rest/progression/subclasses/Discord actions/tests | same as Barbarian A8-A13 | mixed, same split |

### Phase B — Paladin, Druid

| Ticket | Reuses | Codex-safe? |
|---|---|---|
| B1. Paladin creation + equipment | same as A1 | Codex-safe |
| B2. Lay on Hands (healing pool, NOT a spell) | `resource:lay_on_hands` ALREADY EXISTS in content; **must be a `ResourceEngine.spend` + direct HP heal, explicitly NOT routed through `SpellEngine`/spell pool** — this constraint should be a code comment + a test asserting `lay_on_hands` never appears in `reg.spells` | Codex-safe, but the "never a spell" constraint needs a Claude-written test first |
| B3. Divine Sense | passive/narrative-only acceptable for v1 (matches other classes' narrative features) | Codex-safe |
| B4. Fighting Style | same content pattern as Fighter's `fighting_style` (already exists, currently narrative-only) | Codex-safe |
| B5. Prepared casting | `SpellcastingDef.model = PREPARED_SPELLS` — same as Cleric, zero new code | Codex-safe |
| B6. Divine Smite (spend a spell slot on a hit for extra damage) | `ResourceEngine.spend` on the slot resource + `DiceEngine.resolve_damage` — needs a NEW pipeline hook for "convert a committed hit into bonus damage," which is closer to Metamagic's shape than a new system | Claude review (new combat/spell interaction point) |
| B7. Oath selection | `SubclassService` — zero new code, just content (Oath = subclass) | Codex-safe |
| B8. Oath features / Aura features | `SubclassFeatureDef` + a NEW "aura" activation semantics (passive-but-affects-allies-in-range) — **Claude review**: auras are a new mechanical shape (affecting OTHER characters passively) | Claude review |
| B9-B13. Rest/progression/Discord/persistence/tests | same split as Barbarian | mixed |

| Ticket | Reuses | Codex-safe? |
|---|---|---|
| D1. Druid creation + equipment | same as A1 | Codex-safe |
| D2. Prepared casting | same as B5 | Codex-safe |
| D3. Druidic (narrative-only language feature) | matches other narrative features | Codex-safe |
| D4. Wild Shape resource (uses per rest) | `ResourceEngine` — resource definition only | Codex-safe |
| D5. Legal beast-form source + form statistics | **THE hard problem** — beast stat blocks do not exist anywhere in this content pack; this needs the SAME `NPCStatBlock`-style typed content the non-combat-NPC-save ticket recommends, but for beast forms specifically (a `BeastFormDef` in the registry, CR-gated by druid level) | **Claude architecture review required** — this is new typed content + a new registry concept |
| D6. Transformation (swap Character's active combat stats for the form's, reversibly) | Needs a NEW temporary-stat-override mechanism — most architecturally novel item in this whole plan; must NOT mutate the base `Character` row's ability scores/HP max permanently | **Claude review required** |
| D7. Form HP (separate pool that doesn't reduce the Character's real HP) | related to D6 — likely a transient field, not a new persisted resource | Claude review (part of D6's design) |
| D8. Reversion (on 0 form HP, or voluntary) | part of D6 | Claude review (part of D6) |
| D9. Conditions/concentration while transformed (2024: can concentrate while shaped) | `ConcentrationService` — already class-agnostic, should just work once D6 exists | Codex-safe once D6 lands |
| D10. Circle selection | `SubclassService` | Codex-safe |
| D11-D14. Rest/progression/Discord/tests | same split | mixed |

**Recommended order:** Barbarian and Monk first (Phase A) — both are pure
resource/derive-layer work with only 2-3 items needing Claude review each (mostly
"where does this new activation hook live," not new systems). Paladin next (Phase
B, first half) — same shape as Barbarian/Monk plus one new interaction (Smite) and
one new semantics (Aura). **Druid last** — Wild Shape (D5/D6/D7/D8) is a
genuinely new mechanical concept (temporary stat override) that doesn't fit any
existing pattern and deserves its own focused Claude design session before any
Codex ticket in that cluster is opened.

## Live Discord test checklist (manual, requires a real bot token + guild)

Single-player (one Discord account is enough):

- [ ] Wizard: `! ร่าย fire bolt ใส่ <target>` in combat → attack roll, hit/miss,
      damage shown, target HP drops.
- [ ] Cleric: `! ร่าย guiding bolt ใส่ <target>` → attack spell resolves; separately,
      `! ร่าย bless` → no target, concentration begins, shows in `!rv sheet`... (see
      below — sheet does NOT currently show active concentration either; verify
      this gap live).
- [ ] Bard: cast a known spell + verify Bardic Inspiration resource visible
      somewhere (currently NOT wired into any view — expect this to be invisible;
      confirm and note it).
- [ ] Sorcerer: cast a spell, then attempt Metamagic (`apply_metamagic` is
      service-only — **there is no Discord command for it today**; this item will
      fail to demonstrate anything live until a command exists. Flag as blocked,
      don't spend time on it.)
- [ ] Warlock: cast using a pact slot; confirm slot count decrements (no sheet
      display currently shows pact slots by name — verify via `!rv spells` output
      only).
- [ ] Short rest → confirm pact slot returns to max (RestService + ResourceEngine
      already tested at service level; this is the first live confirmation).
- [ ] Subclass selection during level-up: **CANNOT BE TESTED LIVE** — there is no
      Discord command to trigger `level_up` at all (see finding #3 above). Skip
      this checklist item until a level-up command exists; do not spend live-test
      time on it.
- [ ] Restart during subclass selection: same blocker as above — not reachable
      live yet.
- [ ] Character sheet after subclass grant: **will show nothing** — `!rv sheet`
      doesn't read subclass fields (finding #4). Useful to confirm the gap live,
      not to confirm a working feature.
- [ ] Duplicate spell message: send the same cast twice in quick succession (or
      resend after a bot restart) → confirm only one cast lands, one slot spent.
- [ ] Invalid target: `! ร่าย fire bolt ใส่ <nonexistent name>` → clarification or
      "not present" notice, no crash, no slot spent.
- [ ] Missing NPC target statistics: cast a save-based spell at an ambient NPC
      outside combat → the Thai diagnostic fires, nothing consumed (this is the
      CORRECT current behavior per the fail-safe design, not a bug).
- [ ] Final character creation: full guided flow → ✅ สร้างเลย → character created
      exactly once.
- [ ] `!rv resume`: interrupt creation, restart the bot process, `!rv resume` →
      exact same step.
- [ ] `!rv help` (or bare `!rv`) → exactly one response, not duplicated (regression
      check against the existing `_welcome_once` dedup).

**Requires two players:**

- [ ] Two players creating characters simultaneously (draft isolation).
- [ ] One player follows another (`!rv follow`), the leader moves, the follower
      moves too; the OTHER player who didn't follow stays behind.
- [ ] NPC memory: player A helps an NPC, player B threatens it, both later get
      visibly different NPC responses.

## Systems future agents must NOT duplicate

- `app/tabletop/resources/engine.py` — the ONLY resource spend/restore/rest-recharge
  path. Every limited-use ability (Rage, Ki, Lay on Hands, Wild Shape) is a
  `ResourceDef` + `ResourceState`, never a new counter field on `Character`.
- `app/tabletop/spellcasting/engine.py` — the ONLY spell resolution path
  (attack/save/damage/heal/concentration/slot-spend). A new class's spells go
  through `SpellEngine.cast`, never a parallel "class X casts differently" branch.
- `app/tabletop/progression/{subclass,level_up,capabilities}.py` — the ONLY
  subclass/level-up/capability-composition path.
- `app/tabletop/effects/concentration.py` — the ONLY concentration tracker (one
  effect at a time, CON save on damage).
- `app/tabletop/combat/combat_service.py` — the ONLY initiative/turn/attack/HP
  path. Barbarian Rage-ending-on-turn-boundary and Monk's reaction (Deflect
  Missiles) must hook INTO this service's turn/reaction lifecycle, not create a
  second combat loop.
- `app/rules_content/registry.py` — the ONLY typed content schema. A new
  `BeastFormDef`/`NPCStatBlock` (per the Druid/non-combat-NPC recommendations
  above) belongs here, validated at startup like everything else — never as
  untyped JSON blobs read ad hoc by a service.
- `app/orchestration/pipeline.py`'s `_handle_cast`/`_handle_rest`-style domain
  routing — a new mechanical verb (Rage activation, Divine Smite) is a new branch
  in `_process` following that exact shape (resolve → validate → engine call →
  atomic commit → narrate), not a new dispatch mechanism.

## Codex-safe work (can start immediately, no architecture decisions)

- Barbarian/Monk/Paladin/Druid: creation presets, starting equipment content,
  Unarmored Defense `BaseAC` kind additions, Martial Arts die-scaling helper
  (mirrors `bard.py`), Danger Sense/Reckless Attack (both reuse existing
  `advantage=` params), Stunning Strike level-gate, subclass content (features for
  the other 3 stubs per existing class — genuinely fills all 4 subclasses this
  time instead of just 1), rest-recovery resource definitions, all persistence/
  restart tests following `test_subclass_progression.py`'s exact pattern.
- Filling in the OTHER 3 subclasses per EXISTING class (battle_master,
  eldritch_knight, psi_warrior for fighter; the other rogue/wizard/cleric/ranger/
  bard subclasses) with real `SubclassFeatureDef` entries — pure content + typed
  schema, zero architecture risk, directly closes the "1-of-4" finding above.
- Wiring `SubclassService.subclass_features()` into `build_character_sheet` (small,
  additive, no architecture decision — just call an existing method and add a
  field).

## Claude-only work (architecture decisions required)

- Rage/stance representation (`ActiveEffect` vs. `conditions` list) — item A2.
- Any new reaction-trigger hook into `CombatService` (Rage-ending, Deflect
  Missiles) — items A3, M7.
- The new `_handle_*` pipeline branch for Rage/Smite-style mechanical verbs —
  items A11, B6.
- Aura semantics (passive-affects-others) — item B8.
- Wild Shape's temporary-stat-override mechanism — items D5-D8 (the single
  largest remaining architectural gap in the whole class framework).
- The `NPCStatBlock`/`BeastFormDef` typed-content design (non-combat NPC saves +
  Wild Shape forms can likely share one underlying mechanism — worth designing
  together rather than twice).
- AoE target-eligibility/origin-point design (item A in the spell gaps section).
- Wiring `level_up` into an actual Discord command (`!rv level up` or an
  XP-threshold trigger) — currently missing entirely; needed before ANY subclass
  selection or level-progression checklist item can be live-tested.

## Recommended order before Step 5

1. **Wire `level_up` into a Discord command** (small, high-leverage — unblocks 4
   checklist items and makes "level progression works" true end-to-end for the
   first time, not just at the service layer).
2. **Fill in the remaining 3 subclasses per class + sheet subclass display**
   (Codex-safe, closes the "1-of-4" finding, no architecture risk).
3. **Phase A (Barbarian, Monk)** per the plan above.
4. **Phase B first half (Paladin)**, deferring Divine Smite/Aura to Claude review.
5. **Design session for Wild Shape + NPCStatBlock** (Claude-only), then Druid.
6. AoE spells (can happen in parallel with 3-5 once a Claude session picks the
   origin-point design).

Step 5 (whatever it is) should start only after step 1 above, since level-up is
foundational to "subclass selection" ever being demonstrated live.
