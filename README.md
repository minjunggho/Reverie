# Reverie — Thai-first AI Dungeon Master Engine

Discord-first, Thai-first AI Dungeon Master. Friends talk in Discord **voice** (the
bot never touches audio) and **type** to the AI DM in a dedicated channel. Committed
character actions start with `!`. All game mechanics, state, and randomness are
**authoritative and deterministic on the server**; the LLM interprets language and
narrates, but never owns state or randomness.

> **The LLM is not the game state.** See `docs/ai-boundaries.md`.

## Status
MVP under construction. See `PROGRESS.md` for the phase-by-phase status and
`docs/` for the design.

## Layout
```
backend/
  app/            # the engine (services, tabletop, npcs, world, ai, orchestration)
  discord_bot/    # thin discord.py adapter -> app/discord_bridge only
  tests/          # pytest (runs on SQLite, no external services)
  alembic/        # migrations (PostgreSQL target)
docs/             # architecture, domain model, state machine, ai-boundaries, ...
```

## Requirements
- Python 3.11+ (developed on 3.12)
- PostgreSQL (production). Tests use SQLite via `aiosqlite` and need no DB server.

## Setup
```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows Git Bash
pip install -r backend/requirements.txt
cp .env.example .env    # then fill in secrets
```

## Environment variables
All secrets come from the environment (never committed). See `.env.example`.

| Var | Purpose |
|---|---|
| `REVERIE_DATABASE_URL` | async SQLAlchemy URL (e.g. `postgresql+asyncpg://...`). Defaults to a local SQLite file if unset. |
| `REVERIE_LLM_PROVIDER` | `fake` (default), `anthropic`, or `openai` |
| `REVERIE_LLM_MODEL` | model id for the active provider |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | provider API key (only if that provider is active) |
| `DISCORD_BOT_TOKEN` | Discord bot token (only needed to run the live bot) |
| `REVERIE_LOG_LEVEL` | log level (default `INFO`) |

The engine boots and the full test suite passes with `REVERIE_LLM_PROVIDER=fake`
and no external credentials.

## Running
```bash
# API (health + admin/debug)
uvicorn app.main:app --app-dir backend --reload

# Tests
cd backend && python -m pytest -q

# Live Discord bot (requires DISCORD_BOT_TOKEN and a real LLM provider)
python -m discord_bot.run   # from backend/
```

## Playing on Discord

Setup commands use the `!rv` prefix (handled before game actions, so they're never
mistaken for a committed `!`). In your game channel:

```
!rv campaign new <name>        # create a campaign bound to this channel (you = owner)
!rv join                       # join as a player
!rv character <name> [class]   # class: fighter|rogue|wizard|cleric|ranger|bard (default fighter)
!rv session start              # (owner) open a session — recap + opening scene
!rv session end                # (owner) close + player-safe summary
!rv status                     # show table status
!rv help                       # list commands
```

Then play by typing character actions in natural Thai, prefixed with `!`:

```
! ผมค่อยๆ ย่องไปดูหน้าต่าง พยายามไม่ให้ยามเห็น
```

The engine interprets intent, decides the mechanic, rolls the dice **on the server**,
commits the result, and the AI DM narrates it in Thai. Normal messages (no `!`) are
just table talk and change no game state.

## The commitment marker
A message beginning with `!` is an explicit committed character action, written in
natural Thai. Everything else is ordinary table talk and mutates **no** game state.
A message beginning with `!rv` is a table-setup command (see above), not an action.
