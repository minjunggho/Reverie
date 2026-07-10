# Rules Sources & Baseline

## Selected baseline

**SRD 5.2.1** — the System Reference Document for the 2024 revision of the world's
most popular 5th-edition rules, published by Wizards of the Coast LLC under
**Creative Commons Attribution 4.0 International (CC-BY-4.0)**.

- Official landing page: https://www.dndbeyond.com/srd
- Official PDF: https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf
- License: CC-BY-4.0 (irrevocable once published; verified 2026-07-09 via the
  official D&D Beyond SRD page).

### Required attribution (include in any distributed work)
> This work includes material from the System Reference Document 5.2.1
> ("SRD 5.2.1") by Wizards of the Coast LLC, available at
> https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative
> Commons Attribution 4.0 International License, available at
> https://creativecommons.org/licenses/by/4.0/legalcode.

## Source discipline

| Purpose | Acceptable sources |
|---|---|
| Formal mechanics (numbers, procedures, legality) | SRD 5.2.1 PDF only |
| DM technique, pacing, table behavior, UX observations | Experienced-DM material, community writing — never as mechanical authority |
| Thai terminology for game concepts | Product-owned glossary (`app/rules_content/`), reviewed with the table |

**Never** implement formal mechanics from wikis, SEO summaries, Reddit memory, or
forum interpretations. **Never** copy non-SRD proprietary sourcebook content
(non-SRD subclasses, spells, feats, named NPCs/settings) into this repository.

## Versioning scheme

Every mechanical definition in `app/rules_content/` carries:

- `ruleset_id` — `srd521` for this baseline (a future erratum becomes `srd53` etc.)
- `definition_id` — stable key, e.g. `class:wizard`, `spell:mage_armor`
- `definition_version` — integer, bumped on any change to the definition's data

Characters snapshot the `ruleset_id` they were built under. The engine refuses to
mix definitions from incompatible rulesets on one character without explicit
compatibility logic (none exists yet — mixing is simply an error).

## What the repository takes from SRD 5.2.1 (2024 rules)

Implemented as data under `app/rules_content/srd_5_2_1/` (each file cites the SRD
chapter it derives from):

- Character creation order & choices; **backgrounds carry ability score increases
  (+2/+1 or +1/+1/+1 among the background's three abilities) and an Origin feat**
  (2024 change — species no longer grant ASIs).
- Standard Array (15, 14, 13, 12, 10, 8); Point Buy documented (27 points) but not
  yet surfaced in the build UI; rolled scores deferred.
- Proficiency Bonus by level; ability modifiers; the 18 skills; Expertise (double
  proficiency); passive scores (10 + modifier).
- Class chassis at level 1 for: Wizard (flagship), Fighter, Rogue, Cleric, Ranger,
  Bard — hit die, saving throw proficiencies, skill choice lists/counts, armor &
  weapon proficiencies, level-1 features, spellcasting where applicable.
- Wizard spellcasting per the 2024 rules: 3 cantrips at L1; **spellbook with six
  level-1 spells**; **4 prepared spells at L1**; 2 first-level slots; Arcane
  Recovery (once per Long Rest cycle, after a Short Rest, recover slot levels =
  half wizard level rounded up); INT-based Save DC (8 + PB + INT) and spell attack
  (PB + INT).
- Species: Human, Elf, Dwarf, Halfling with their SRD 5.2.1 traits (e.g. Darkvision,
  Dwarven Resilience/Toughness, Halfling Luck/Brave/Nimbleness, Elf Fey Ancestry/
  Keen Senses/Trance, Human Resourceful/Skillful/Versatile).
- Backgrounds (SRD set used): Sage, Soldier, Criminal, Acolyte — abilities trio,
  two skill proficiencies, tool proficiency, Origin feat, equipment package.
- Death & dying: 0 HP → Unconscious+dying; death saves DC 10; 3 successes =
  Stable, 3 failures = dead; nat 1 = two failures; nat 20 = regain 1 HP; damage
  while dying = 1 failure (2 on crit); instant death when excess damage ≥ max HP;
  healing any amount ends dying and resets saves.
- Damage: typed components; Resistance halves (round down), Vulnerability doubles,
  Immunity zeroes — **per component, before totaling**; Temporary HP absorbs first,
  doesn't stack (keep higher), lost before real HP, not restored by healing.
- Concentration: one effect at most; ends on casting another concentration effect,
  on Incapacitated/death, voluntarily (free), or on a failed CON save when taking
  damage — DC = max(10, floor(damage/2)) per damage event.
- Rests: Short Rest = 1+ hour (spend Hit Dice: roll die + CON mod each; some
  features recharge); Long Rest = 8 hours (HP to max, regain half max Hit Dice
  min 1, spell slots, Exhaustion −1, long-rest features); an interrupted rest
  confers no benefits and must be restarted (engine treats a perceivable world
  event during the window as the interruption trigger).

## Known deviations / simplifications (documented, deliberate)

- Only level 1 is fully data-driven today; the level table scaffolding exists but
  levels 2+ content is not yet entered (level-up workflow is designed, not shipped).
- Subclasses (chosen at level 3 in 2024 rules) are not yet represented.
- Feats: Origin feats are recorded as named grants; their mechanical effects are
  not yet executable (documented on the grant).
- Encumbrance/carry weight: out of MVP scope.
- Point Buy and rolled ability scores: documented, not yet in the build flow.
