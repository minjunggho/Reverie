# PROGRESS — Reverie

**Status:** MVP complete + **player/DM experience overhaul** implemented.
All 13 phases + the §34 vertical slice + §31/§32 concurrency/recovery + the
experience overhaul (docs/experience-overhaul.md) are covered by **88 passing
automated tests** (`cd backend && python -m pytest -q`), including the two-player
acceptance journey (`tests/test_acceptance_journey.py`).

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
