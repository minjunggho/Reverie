# PROGRESS — Reverie

**Status:** MVP + experience overhaul + **rules-complete evolution (SRD 5.2.1)**.
**107 passing automated tests** (`cd backend && python -m pytest -q`), including
the two-player acceptance journey with the full guided character build and the
dice ritual.

## Rules evolution (2026-07-09) — what changed
Baseline researched and documented: **SRD 5.2.1, CC-BY-4.0** (docs/rules-sources.md,
docs/rules-coverage-audit.md, docs/next-architecture.md).

- **Rule Definition System**: versioned JSON content (`app/rules_content/srd_5_2_1/`)
  — 6 classes (L1 chassis), 4 species with traits, 4 backgrounds (2024-style: ASI
  trio + Origin feat + 2 skills + tool + equipment), 30 spells with Thai summaries,
  resource definitions. Loaded by a validated read-only registry
  (ruleset_id/definition_id/definition_version).
- **Character v2**: species/background split from backstory; saves, Expertise,
  languages, tool proficiencies, temp HP, speed, Hit Dice, death saves, Exhaustion,
  dying/stable/dead. Grant provenance (`CharacterGrant` — "where did I get this?"),
  spells with kind/prepared state, `ResourceState`, `ActiveEffect`.
- **Derivation engine** (`tabletop/rules/derive.py`): every derived value computed
  with an explainable breakdown — `!rv skill arcana` answers "ทำไมถึง +5" with the
  actual composition. HP/AC/passives/spell DC never AI-assigned.
- **Resource engine + rests**: generic max formulas + recharge semantics (long rest,
  short-rest partial, Arcane-Recovery cycle). Short/Long Rest are domain operations
  on the world clock; a perceivable world event inside the window **interrupts** the
  rest (no benefits, 2024 restart rule).
- **Damage pipeline**: typed components resolved independently (Resistance/
  Vulnerability/Immunity before totaling), temp HP absorbs first (keep-higher),
  dying/death saves/instant death per SRD, healing revives; damage triggers
  **Concentration saves** (DC max(10, dmg/2)); one concentration effect at most.
- **Two-stage creation**: Stage A conversation → reflection card (facts heard, no
  mechanics) → Stage B guided build where the **player chooses everything** —
  class/species/background (⭐ recommended, never auto-applied), Standard Array
  arrangement, background ASI, class skills (legal counts, duplicate handling),
  species trait choices, Rogue Expertise, cantrips/spellbook/prepared — finalized
  in one transaction with full provenance + starting gear.
- **Dice ritual**: campaign `dice_mode` (default PLAYER_CLICK) — visible checks
  pause at a CHECK_PROMPT [🎲 ทอย d20]; the SERVER rolls on tap; ROLL and NARRATION
  arrive as separate messages. AUTO preserves immediate resolution. Hidden checks
  stay silent.
- **Clarification restraint**: engine gate — an adjudicator's clarification is
  honored only when the interpreter also found material missing info ("! แอบฟังต่อไป"
  with an established conversation never asks "ฟังเรื่องอะไร?"). Prompts updated
  with the counter-example; A/B/C choice prompts banned in narration.
- **Failure with teeth**: `Scene.allowed_clues` + `reveal_fragment` delta — the
  model may *time* a partial reveal ("...ไม่ใช่ของมนุษย์") but only from authored
  clue text; inventions are rejected.
- **Views v2**: full sheet (saves ●, Expertise ★, passives, initiative, speed, Hit
  Dice, death saves, spell DC/slots), `!rv spells` (cantrips/spellbook/prepared ✦/
  concentration banner), `!rv skill <name>` breakdown.
- DB additions (additive; delete stale dev sqlite): character v2 columns,
  character_grants, character_spells, resource_states, active_effects,
  scenes.allowed_clues.

**Deferred (designed in docs/next-architecture.md §8):** level-up + subclasses +
executable feats (E2), spell execution engine (E3), combat economy v2 (E4),
campaign Markdown import + DM canon split + blind-owner mode (E5), Grimoire
Activity + DM Studio (E6), Homebrew Lab (E7).

## Experience overhaul (2026-07-09) — what changed
- **Presentation contract**: every player-facing message is kinded
  (`app/presentation`, 21 kinds incl. REVERIE_WELCOME…TECHNICAL_ERROR); the Discord
  adapter renders embeds/colors/buttons (`discord_bot/render.py`); button clicks
  round-trip as typed text (uniform + testable).
- **Guided character creation**: `!rv character` opens a Thai conversation
  (CharacterDraft state machine + CreationGuidance job) → hooks (origin/desire/
  fear/flaw/connection/appearance) stored on Character → CHARACTER_REVEAL with
  class preset + starting gear. Quick path preserved.
- **Session Zero**: `!rv setup` — 4 friendly questions → campaign profile
  (tone/balance/assistance/boundaries).
- **Openings**: Session 1 is AI-generated from a bounded context (profile +
  character hooks + location) and must tie in an established hook; sessions ≥2 get
  recap + place/time continuity. No hardcoded tavern.
- **Readable resolution**: one CHECK_RESOLUTION message = short narration lines +
  visible engine-owned dice line + optional decision prompt. Mechanics never in prose.
- **Error staging**: bridge consults ProcessedMessage.stage — pre-commit failures
  say "nothing happened, retry"; post-commit narration failures restate the
  committed result and never re-execute. No more bare "internal error".
- **Views**: `!rv sheet / inventory / journal / party` (journal derives from
  player-visible events — leak-proof by construction). Items: ItemDefinition/
  InventoryEntry + class starting gear + ITEM_GAINED events.
- **NPC dialogue**: CHARACTER_DIALOGUE at a visible NPC routes to the epistemic
  social service (NPC_DIALOGUE kind, named speaker).
- **Private secrets**: `reveal_secret` consequence delta can only point at a
  PRE-AUTHORED Secret row; delivery is an engine-enforced DM (PRIVATE_SECRET).
- **Closing**: deliberate beat → SESSION_END chronicle (decisions/discoveries/
  items/objectives) → one-tap feedback stored on Session.feedback.
- **Narration policy**: progressive disclosure + banned stock phrases
  (docs/thai-dm-style.md); manual eval fixtures in `backend/evals/`.
- **DB (additive)**: characters.hooks/appearance, character_drafts,
  item_definitions, inventory_entries, sessions.feedback; campaign.config gains
  profile/setup_state keys.

## Key architectural decisions
- **Tests run on SQLite (`aiosqlite`)**; production targets PostgreSQL (`asyncpg`).
  ORM kept dialect-portable (string-UUID PKs, portable JSON). Alembic targets PG.
- **In-process async lock per session** for the serialized action queue (single bot
  process assumed for MVP). Redis deferred until multi-process deployment.
- **LLM never touches state or randomness.** All AI jobs return Pydantic proposals;
  the engine validates + commits. `FakeLLMProvider` drives all tests deterministically.
- Primary keys are `uuid4().hex` strings generated in Python (portable, testable).
- Optimistic concurrency is **manual** (`guarded_version_update` + `version` columns),
  which is explicit and testable.

## Phase status — all complete

| Phase | Goal | Status | Tests |
|---|---|---|---|
| 0 | Docs + scaffolding | ✅ | — |
| 1 | App core, config, DB, health, harness (FakeLLM + deterministic dice) | ✅ | test_phase1_core |
| 2 | Campaign/member/character/session/scene | ✅ | test_phase2_domain |
| 3 | Event model + atomic state+event transactions | ✅ | test_phase3_events |
| 4 | Discord bridge + idempotency + identity + serialized queue | ✅ | test_phase4_bridge |
| 5 | Normal vs `!` routing + TableMessageClassifier | ✅ | test_phase5_routing |
| 6 | ActionInterpreter + clarification model | ✅ | test_phase6_clarification |
| 7 | Adjudication + authoritative dice + committed pipeline | ✅ | test_phase7_dice, test_phase7_pipeline |
| 8 | Thai DM narration + style + consequence model | ✅ | test_phase8_narration |
| 9 | Session opening + player-safe recap | ✅ | test_phase9_opening_recap |
| 10 | Session closing + post-session continuity pipeline | ✅ | test_phase10_post_session |
| 11 | NPC knowledge/belief + basic social | ✅ | test_phase11_npc |
| 12 | World time + threats/scheduler | ✅ | test_phase12_world |
| 13 | Basic combat (initiative/turns/attack/damage/interrupt) | ✅ | test_phase13_combat |
| — | §34 first vertical slice (end-to-end) | ✅ | test_slice_vertical |
| — | §31/§32 concurrency + recovery | ✅ | test_concurrency_recovery |

## §34 first vertical slice — checklist (all covered by test_slice_vertical.py)
- [x] init app / campaign / two members / one character each / location / guard NPC
- [x] start Session 1 / opening scene framed in Thai
- [x] normal message executes NO committed action, mutates NO state, emits NO events
- [x] `!` action: resolve user→character, interpret, decide uncertainty, select Stealth
- [x] server d20 + modifier + DC compare + atomic commit (state+event)
- [x] Thai narration from committed result; scene at rest afterward
- [x] end session; player-safe recap; NO DM-only info in recap

## Definition of Done (§38) — met
- [x] §34 vertical slice passes end-to-end with automated tests
- [x] normal vs `!` routing proven
- [x] LLM provably cannot roll dice, compute modifiers, or mutate canonical state
- [x] information-safety tests pass (recaps + NPC prompts cannot leak)
- [x] concurrency + idempotent recovery tests pass
- [x] opening → active play → closing → post-session with player-safe + private
      continuity artifacts derived from canonical events
- [x] basic social and basic combat function
- [x] docs + PROGRESS reflect the built system

## Supported rules subset (documented in app/tabletop/rules/core.py)
Ability checks, saving throws, single-weapon attacks + damage, flat proficiency bonus
by level, a fixed skill→ability map, a small class/ancestry allowlist. Consequence
deltas limited to a validated allowlist (`advance_time`, `raise_suspicion`, `note`) —
damage/HP only ever come from the deterministic dice/combat path. NOT supported:
spells, subclasses, feats, multiclassing, the full condition set.

## Runnable surfaces
- API: `uvicorn app.main:app --app-dir backend` (health + read-only admin/debug).
- Bot: `python -m discord_bot.run` (from backend/; needs DISCORD_BOT_TOKEN + a real
  LLM provider). Thin adapter over `app.discord_bridge`.
- Composition root: `app.engine.build_bridge` / `build_default_bridge`.

## Deferred (with reason)
- **Redis distributed lock** — not needed for single-process MVP; in-process lock used.
  Swap in for multi-process deployment.
- **Concrete Alembic migration scripts** — env/wiring is in place (async, targets PG);
  run `alembic revision --autogenerate -m "initial schema"` to generate. Tests use
  `create_all` on SQLite and don't depend on migrations.
- **AI-inferred commitment detection** — intentionally not built; only `!` commits.
  The `ActionCommitment` abstraction + reserved `CommitmentSource` values leave room.
- **`failure_progress_level` config flag** — stored and available; the engine never
  auto-escalates a miss (satisfying "never auto-convert every miss"), but explicit
  band-downgrade enforcement from the flag is left as a tuning hook.
- **Full pipeline↔combat wiring** — the combat engine is complete and tested; routing
  a Thai committed action to an in-progress encounter (target resolution from text) is
  the one remaining integration seam. Social responses likewise have a service
  (`NPCSocialService`) ready to be called from the router.
- **NPC memory / suspicion as separate tables** — consolidated into `NPCFact.status`
  (KnowledgeStatus) + `NPCRelationship`; separate `NPCMemory`/`NPCSuspicion` tables
  are a future normalization, not a capability gap.

## How to run
```
cd backend
python -m pytest -q          # 74 tests
uvicorn app.main:app         # from backend/ (or: uvicorn app.main:app --app-dir backend)
```
