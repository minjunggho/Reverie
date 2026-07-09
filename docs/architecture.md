# Architecture — Reverie (Thai-first AI Dungeon Master Engine)

## One-sentence summary
Reverie is a Discord-first, Thai-first AI Dungeon Master. Friends talk in Discord
voice (never processed by the bot) and **type** to the AI DM in a dedicated text
channel. All game mechanics, state, and randomness are authoritative and
deterministic on the server. The LLM interprets language and narrates; it never
owns state or randomness.

## The non-negotiable law
> **THE LLM IS NOT THE GAME STATE.** The LLM never invents or mutates authoritative
> values (HP, AC, XP, gold, inventory, dice results, damage, initiative, quest
> status, NPC/character death, world time, campaign clocks). It interprets intent,
> proposes *structured typed* decisions, and narrates *already-committed* results.
> Every state change and every random number is executed by deterministic server
> code inside a database transaction.

## Layering (dependency direction points inward)

```
discord_bot/  (discord.py client, thin)         <- I/O edge, no game logic
     |  calls
     v
app/discord_bridge/  (application-facing bridge)  <- translates Discord I/O <-> engine DTOs
     |  calls
     v
app/orchestration/  (committed-action pipeline, per-session queue)
     |  uses
     v
app/services/ + app/tabletop/ + app/npcs/ + app/world/  (THE ENGINE: all game logic)
     |  uses
     v
app/models/ (SQLAlchemy canonical state) + app/db/  (transactions)
```

- **No Discord types leak inward.** `discord_bridge` accepts primitive/DTO input
  (`InboundMessage`) and returns `OutboundMessage`. The bot never imports engine
  internals; the engine never imports `discord.py`.
- **No game logic in Discord handlers.** Handlers parse a Discord event into an
  `InboundMessage` and hand it to the bridge.
- **AI is a leaf, not a hub.** `app/ai/` is called *by* the engine at specific
  decision points. AI jobs return Pydantic-validated proposals; the engine
  validates and commits. AI never writes to the DB.

## Key subsystems

| Package | Responsibility |
|---|---|
| `app/core` | settings (env), logging, errors, id/clock/randomness abstractions |
| `app/db` | async engine, session factory, `Base`, `unit_of_work` transaction helper |
| `app/models` | canonical ORM state (Campaign, Character, Event, Scene, ...) |
| `app/schemas` | Pydantic DTOs + **all** LLM I/O schemas |
| `app/services/*` | the engine's public boundary (campaigns, sessions, scenes, events) |
| `app/tabletop/dice` | authoritative server dice behind a `Randomness` abstraction |
| `app/tabletop/rules` | small documented D&D 5e-flavored subset (modifiers, skills, proficiency) |
| `app/tabletop/adjudication` | RollNecessityJudge, MechanicSelector, DCResolver, ConsequencePlanner |
| `app/tabletop/combat` | basic encounter/turns (Phase 13) |
| `app/npcs` | NPC state + epistemic records (knowledge/belief/suspicion/memory) |
| `app/world` | locations, world time, threats/scheduler |
| `app/memory` | per-task context builders (each enforces visibility) |
| `app/knowledge` | information visibility & provenance (retrieval-layer authorization) |
| `app/ai/llm` | `LLMProvider` abstraction + providers + `FakeLLMProvider` |
| `app/ai/jobs` | the AI jobs (classifier, interpreter, adjudicator, narrator, recap, ...) |
| `app/ai/prompts` | prompt templates incl. Thai DM style |
| `app/orchestration` | the committed-action pipeline + per-session serialized queue |
| `app/discord_bridge` | application-facing bridge the bot calls |

## Data & truth model
- **Authoritative relational state** (Character, InventoryEntry, Quest, Threat,
  world time, combat) = source of truth for *current values*.
- **Event log** = canonical append-only history of *meaningful occurrences*.
- Every canonical mutation is written **in the same transaction** as the Event(s)
  that record it (`unit_of_work`). Either both commit or both roll back.
- Raw Discord messages may be stored for audit but are **not** automatically
  canonical events.
- Generated prose (recaps, narration, summaries) is **never** the canonical DB;
  it is a derived view over events + state.

## AI job architecture
Not one giant "You are the DM" prompt, and not a multi-agent swarm. A small set
of single-responsibility jobs, each with PURPOSE / INPUT / CONTEXT-BUILDER /
STRUCTURED-OUTPUT / ALLOWED-TOOLS / FORBIDDEN / FALLBACK. See `ai-boundaries.md`.

## Concurrency policy
Correct ordering beats parallel throughput. Committed (`!`) actions for one active
session are processed **one at a time in arrival order** by a per-session serialized
queue; each action reads fresh committed state. Non-committed messages mutate
nothing and may be handled with more concurrency. Idempotency by
`discord_message_id` (`ProcessedMessage`). Optimistic concurrency via `version`
columns on Session/Scene.

## Persistence / dialect note
Production target is PostgreSQL (async `asyncpg`). The ORM is written to be
dialect-portable (JSON columns, string UUID PKs) so the **test suite runs against
`aiosqlite`** with no server required. This is a deliberate testability decision,
documented here and in `PROGRESS.md`. Alembic migrations target PostgreSQL.

## What is explicitly NOT built (MVP hard exclusions)
Voice capture / STT / TTS / diarization; generated maps or art; music/ambient;
Discord Activity UI; mobile/web player; full D&D ruleset; autonomous web browsing;
multi-agent swarms; AI-inferred action commitment (only explicit `!` prefix).
