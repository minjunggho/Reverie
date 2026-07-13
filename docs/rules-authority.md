# Rules authority

Reverie has **one authoritative tabletop ruleset**. Every mechanical decision —
ability scores, proficiency, HP/AC, saves, skills, spellcasting, resources, rest,
combat — resolves against it. There is no silent mixing of editions, no homebrew
in the engine, and no invented mechanics.

## The ruleset

**D&D 2024 (System Reference Document 5.2.1).**

- `RULESET_ID = "srd521"` — the content pack id.
- `RULESET_EDITION = "D&D 2024 (SRD 5.2.1)"` — the human-readable edition.
- Content lives under `backend/app/rules_content/srd_5_2_1/`.
- The live value is reported by `!rv diagnostics` (`rules: srd521 …`) and stamped
  in `manifest.json` (`rules_edition`), so a deployment can be proven to run the
  documented edition.

Why 2024 SRD, specifically: it is the current licensed SRD, and the content this
project already authored (backgrounds granting an Origin Feat + a +2/+1 or +1/+1/+1
ability spread, species without fixed ability bonuses, weapon mastery-era classes,
the 2024 rest and concentration rules) is 2024, not 2014. The engine's rest rule
(an interrupted rest confers no benefit) and background-driven ability increases
are 2024-specific and are implemented as such.

## Configurability

The ruleset is **selected by content pack**, not hard-coded across the engine:
services read typed definitions from the `RulesRegistry`, keyed by `RULESET_ID`.
Supporting a second edition later means adding a content directory + manifest and
selecting it — not editing scattered class logic. For this phase exactly one
ruleset (`srd521`) is authoritative and loaded.

## What "authoritative" forbids

- No 2014 numbers where 2024 differs (e.g. backgrounds, ability increases, rest).
- No mechanic the engine cannot resolve honestly. A spell is only selectable if the
  spell engine can resolve it; a class is only selectable if its class-specific
  mechanics are implemented and tested (see `docs/class-framework.md`).
- No number produced by narration. The LLM never rolls, never computes a modifier,
  never sets mechanical state — the deterministic engine does.

## Where the authority is enforced

- `app/tabletop/rules/core.py` — abilities, proficiency bonus, skill→ability map.
- `app/tabletop/rules/derive.py` — HP/AC/saves/skills/spell DC, all registry-driven.
- `app/rules_content/registry.py` — the typed class/species/background/spell/
  resource/feature definitions + startup validation (`RulesViolation` on any
  incoherent content, e.g. a selectable class whose spell pool can't satisfy its
  required counts).
- `app/tabletop/{resources,spellcasting,combat,rest,effects}/` — one reusable
  implementation per mechanical system, shared by every class.
