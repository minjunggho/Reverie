# Next Architecture — the Rules-Complete Evolution

Design for the post-overhaul evolution. Preserves every existing law: the LLM is
not game state, the server owns dice, services own mutation inside transactions
paired with events, retrieval enforces visibility, Discord is an adapter.

## 1. Layer map (new pieces in **bold**)

```
rules content (versioned JSON, srd521)   ← **RulesRegistry** (read-only, validated)
        │ definitions
        ▼
**derivation engine** (pure functions: modifiers, saves, skills w/ breakdown,
                       passives, spell DC/attack, max HP, resource maxima)
        │ reads
        ▼
canonical state (PostgreSQL/SQLite): Character v2, **CharacterGrant**,
   **CharacterSpell**, **ResourceState**, **ActiveEffect**, Scene(+clues), ...
        ▲ mutations only through services in unit-of-work + events
        │
**ResourceEngine** · **RestService** · **DamageService** · **ConcentrationService**
        ▲ invoked by
orchestration (pipeline w/ **pending-check dice ritual**, flows) ← AI proposals
```

**Definitions vs grants vs state.** A `ClassDefinition` says what Wizard L1 *offers*
(choices, features, resources). A `CharacterGrant` records what THIS character
*received and from where* (`source_type`: CLASS/SPECIES/BACKGROUND/FEAT/ITEM/
CAMPAIGN_HOMEBREW/TEMPORARY_EFFECT). Current state (`hp`, `ResourceState.current`,
prepared flags) lives in its own rows. Derived values (skill bonuses, save DC) are
**never** stored as the only truth — the derivation engine computes them and can
explain them ("ทำไม Arcana +5: INT +3, Proficiency +2").

## 2. Rules content (`app/rules_content/`)

JSON (stdlib-parseable, diff-able, versioned), one file per definition family,
loaded once into a validated `RulesRegistry` (pydantic models). Every record has
`ruleset_id/definition_id/definition_version`. Markdown never executes; JSON never
narrates. Thai display strings (`name_th`, `pitch_th`, `explain_th`) live beside
mechanics so the build UX and views don't hardcode copy.

Resource max formulas are small data expressions, not code:
`{"kind":"flat","value":2}` · `{"kind":"by_class_level","table":{"1":2,...}}` ·
`{"kind":"ability_mod_min_1","ability":"cha"}` · `{"kind":"half_level_round_up"}`.
Recharge: `short_rest` | `long_rest` | `long_rest_cycle_after_short_rest` (Arcane
Recovery) — extensible enum, engine-interpreted.

## 3. The dice ritual (pending-check state machine)

Player-visible checks stop auto-resolving. Pipeline splits at RESOLVING:

```
ADJUDICATED ──(dice_mode=AUTO or hidden/passive check)──► roll now (old path)
     │
     └─(PLAYER_CLICK, visible player check)──► persist PendingCheck on scene
            → send CHECK_PROMPT (skill, modifier breakdown, [🎲 ทอย d20] button)
            → player's click/text resumes the SAME serialized pipeline:
              server rolls → commit (state+events atomic) → CHECK_RESULT message
              → separate narration message
```

ROLL and NARRATION are separate presentation objects (mandate §28). Hidden checks
(NPC stealth vs passive perception, secret DM checks) resolve silently server-side.
`campaign.config.dice_mode` defaults to `PLAYER_CLICK`.

## 4. Two-stage creation

Stage A (exists, improved): concept conversation → **reflection card** (facts the
AI heard, [ถูกต้อง]/[แก้ไข]) → `CharacterConceptDraft` confirmed. The AI may
summarize; it may not invent accepted facts.

Stage B (new, deterministic engine, zero LLM dependency): an ordered choice walk
driven entirely by the registry — CLASS → SPECIES → BACKGROUND → ability scores
(Standard Array: recommended arrangement per class primaries, [ใช้แบบแนะนำ]/[จัดเอง])
→ background ASI trio (+2/+1 or +1+1+1) → class skill choices (legal list, count
enforced, duplicates trigger replacement per 2024 rule) → cantrips → spellbook →
prepared → equipment package → FINAL REVIEW → finalize. Recommendations are ranked
engine-side from concept keywords and rendered with each definition's `pitch_th`;
`RECOMMENDED` is labeled, never auto-applied. Every step accepts `ยกเลิก`.

Finalize = one transaction: Character v2 row + grants + spells + resource states +
starting equipment + hooks from Stage A. HP/AC/etc. derived, never AI-assigned.

## 5. Damage, concentration, death (event-ordered pipeline)

`DamageService.apply(target, components[], source)`:
1. per-component defense lookup from grants (resistance half↓, vulnerability ×2, immunity 0)
2. total → Temp HP absorbs first → HP reduction (events with full breakdown)
3. HP 0: excess ≥ max HP → dead; else dying (unconscious, death-save state reset)
4. damage while dying → death-save failure(s) instead
5. if target concentrating → CONCENTRATION_SAVE_REQUIRED (DC max(10, dmg//2)),
   resolved by ConcentrationService via the dice engine (CON save), failure ends
   the effect with its own event
Healing: cap at max, any amount ends dying + resets saves; never restores Temp HP.
Narration receives the committed component breakdown; it cannot re-price damage.

## 6. Rests are domain operations on the world clock

`RestService.short_rest/long_rest`: validates preconditions, advances the world
clock through the rest window; if a *perceivable* scheduled event fires inside the
window → `REST_INTERRUPTED` (no benefits, per 2024 restart rule) with the event
surfaced for narration. Completion applies recharges via ResourceEngine + Hit-Dice
spending (short) or the long-rest bundle (HP max, half Hit Dice min 1, slots,
Exhaustion −1, re-preparation window). Natural input "! พวกเราพักตรงนี้สักชั่วโมง"
is interpreted by the AI into a rest *intent*; the service owns everything after.

## 7. Failure with teeth — authored fragments only

`Scene.allowed_clues` (authored strings, seeded from location/campaign clue data).
The ConsequencePlanner may propose `reveal_fragment` on failure; the DeltaApplier
validates the fragment is a substring/exact match of an authored clue before
committing a PARTY-visible KNOWLEDGE_GAINED. The LLM can *time* a reveal, never
*author* lore. Suspicion/time/noise deltas already exist and stay.

## 8. Roadmap of remaining slices (design-complete, not yet shipped)

| Slice | Content |
|---|---|
| E2 | Level-up workflow (guided grants from level tables), subclasses at L3, executable Origin feats |
| E3 | Spell execution engine: areas, durations, upcasting, ritual; effect primitives beyond damage/heal/condition/resource/reveal |
| E4 | Combat economy v2: actions/bonus/reactions, movement+OA, condition mechanical effects |
| E5 | Campaign Markdown import → structured DM canon (factions, NPCs, secrets, clues, revelation paths) with import review; player-safe vs DM canon authorization split extended; blind-owner mode |
| E6 | Grimoire Activity (mobile-first player binder) + DM Studio (role-gated) — backend APIs return only authorized data; no CSS-hiding of secrets |
| E7 | Homebrew Lab: classify (existing / reskin / new mechanic), AI proposes → primitives validate → owner approves |

Storage responsibilities (mandate §10): mechanics = versioned JSON content; runtime
state = PostgreSQL; long-form authoring = Markdown (imported, never executed);
derived values = computed with explanations, cached only when measured-necessary.
