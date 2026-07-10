# Rules Coverage Audit — current engine vs SRD 5.2.1 requirements

Audited: 2026-07-09, against commit `7a936e2` (88 tests green). Legend:
✅ adequate for now · 🟡 exists but shallow · ❌ missing · 🔵 shipped in this evolution.

## Character representation

| System | Before | Now | Notes |
|---|---|---|---|
| Ability scores + modifiers | ✅ | ✅ | engine-derived |
| Species | 🟡 name only ("ancestry") | 🔵 | species definition + traits as grants (Darkvision etc.) |
| Class / level | 🟡 name+level | 🔵 | data-driven L1 chassis; level table scaffold |
| Subclass | ❌ | ❌ (designed) | 2024: chosen at L3 — deferred with level-up |
| Rules Background (vs backstory) | ❌ conflated | 🔵 | distinct: background grants ASI trio choice, 2 skills, tool, Origin feat, equipment |
| Backstory/hooks | ✅ (overhaul) | ✅ | kept separate from Background |
| Proficiency bonus | 🟡 stored int | ✅ | derived from level |
| Saving throw proficiencies + modifiers | ❌ | 🔵 | from class definition; derived bonuses |
| 18 skills + proficiency | 🟡 (had all 18 names) | 🔵 | choices legality engine-enforced |
| Expertise | ❌ | 🔵 | representation + derivation + Rogue L1 choose-2 step in the build flow |
| Passive Perception (and passives) | ❌ | 🔵 | 10 + derived modifier |
| Initiative | ❌ | 🔵 | derived (DEX mod) |
| Speed | ❌ | 🔵 | from species |
| HP / Max HP | ✅ | ✅ | L1 = hit die max + CON mod on finalize |
| Temporary HP | ❌ | 🔵 | absorb-first, keep-higher, not healable |
| Hit Dice (total/remaining) | ❌ | 🔵 | spend on short rest, regain half on long |
| Death saves / dying / stable / instant death | ❌ | 🔵 | full SRD procedure |
| Conditions | 🟡 string list | 🟡 | list kept; mechanical hooks: Incapacitated ends concentration; full condition effects deferred |
| Exhaustion | ❌ | 🔵 | level 0–10 tracked; −1 per long rest; mechanical penalties deferred |
| Languages / armor / weapon / tool proficiencies | ❌ | 🔵 | granted with provenance |
| Attacks | 🟡 combat-only | 🟡 | combat engine unchanged this slice |
| Feats / Origin feats | ❌ | 🟡 | recorded as named grants (not yet executable) |
| Grant provenance ("where did I get this?") | ❌ | 🔵 | CharacterGrant(source_type, source_key) |
| Alignment / XP | 🟡 | 🟡 | fields exist; milestone-first table |

## Spellcasting (Wizard flagship)

| System | Before | Now |
|---|---|---|
| Cantrips (known, source) | ❌ | 🔵 3 at L1, chosen from wizard list, provenance CLASS |
| Spellbook | ❌ | 🔵 6 level-1 spells at creation |
| Prepared spells | ❌ | 🔵 4 at L1; re-preparable after Long Rest |
| Spell slots | ❌ | 🔵 resource-engine pool (L1: 2) |
| Spell Save DC / Spell Attack | ❌ | 🔵 derived (8+PB+INT / PB+INT) |
| Concentration | ❌ | 🔵 tracked ActiveEffect; save on damage DC max(10, dmg/2); one at a time; ends on incapacitation/death |
| Upcasting / durations / areas | ❌ | ❌ designed; spell execution beyond adjudicated checks deferred |
| Ritual casting | ❌ | ❌ deferred |

## Action economy & combat

Unchanged this slice (basic combat from Phase 13 stands): initiative, turns,
attack/damage/HP, one interrupt. 🟡 Reactions/bonus-actions as general economy,
opportunity-attack triggers from movement, crits beyond nat-20 doubling — deferred
to the combat evolution slice.

## Damage & healing

| System | Before | Now |
|---|---|---|
| Typed damage components | ❌ flat ints | 🔵 per-component type |
| Resistance / Vulnerability / Immunity | ❌ | 🔵 per component, before totaling (from trait grants) |
| Temp HP interaction | ❌ | 🔵 |
| 0-HP transition, dying, instant death | ❌ | 🔵 |
| Concentration trigger from damage | ❌ | 🔵 CONCENTRATION_SAVE_REQUIRED emitted + resolved |
| Healing (cap, dying recovery) | 🟡 | 🔵 |

## Resources & rests

| System | Before | Now |
|---|---|---|
| Generic ResourceDefinition/State | ❌ booleans nowhere | 🔵 max formulas (flat/by-level/ability), recharge triggers (short/long rest, partial) |
| Spell slots as resources | ❌ | 🔵 |
| Arcane Recovery (partial, post-short-rest, 1/long-rest cycle) | ❌ | 🔵 |
| Second Wind / Bardic Inspiration examples | ❌ | 🔵 definitions present |
| Short Rest (1h, Hit Dice, world clock advances, interruptible) | ❌ | 🔵 |
| Long Rest (8h, HP, half Hit Dice, slots, Exhaustion −1, interruptible) | ❌ | 🔵 |
| Per-turn resources | ❌ | ❌ deferred with combat economy |

## Adjudication & experience (this evolution's UX mandates)

| Mandate | Status |
|---|---|
| Clarify only when material (the "แอบฟังต่อไป" failure) | 🔵 engine gate + prompt restraint with that exact counter-example |
| Failure changes the scene (partial fragments from AUTHORED clues only) | 🔵 `reveal_fragment` delta validated against scene's authored clue fragments |
| No A/B video-game prompts | 🔵 prompt policy: open "จะทำอย่างไร?" unless mechanical choice/beginner asks |
| Dice as ritual (player-click roll, separate ROLL vs NARRATION messages) | 🔵 PLAYER_CLICK default for player-visible checks; AUTO configurable; hidden/passive checks stay silent |
| AI recommends / player chooses (class, species, background, abilities, skills, spells) | 🔵 Stage B guided build — every mechanical decision is a player button/text choice with Thai explanations |
| Sheet exposes full capabilities | 🔵 sheet v2 + `!rv spells` + `!rv skill <name>` breakdown ("ทำไม +5") |

## Deferred (designed in docs/next-architecture.md, not shipped this slice)

Level-up workflow · subclasses · executable feats · full condition effects ·
combat economy v2 · spell execution engine (areas/durations/upcast) · homebrew lab ·
effect-primitive engine beyond damage/heal/condition/resource/reveal · campaign
Markdown import + DM Studio + Grimoire Activity · blind-owner mode · point-buy/roll.
Each has a design section and a slice number in the roadmap.
