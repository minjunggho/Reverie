# E7 — Campaign-Agnostic Reverie (player-centered revamp, slice 1)

Status: **SHIPPED** (178 tests green). This slice makes Reverie campaign-agnostic,
adds AI-assisted campaign creation, authoritative money, visible time, and deploy
verification. It is the first slice of the full player-centered revamp; the
remaining program is at the bottom.

## Root causes fixed

1. **The universal tavern.** `!rv session start` on a campaign with no location
   invented `โรงเตี๊ยมหมาป่าเทา` (admin_bridge). Every campaign that skipped import
   became the Grey Wolf Tavern game. → Removed. No replacement default exists.
2. **Creation order treated as campaign intent.** Session start used
   `LocationService.latest_location` ("most recently created") as the party's
   position. Any world expansion or import re-ordering silently moved the party.
   → `latest_location` is deleted; the only count-based fallback is
   `only_location` (a campaign with exactly ONE location is unambiguous).
3. **Session prep teleported the party home.** `session_prep.opening_location_id`
   overrode the opening location on EVERY session, so session 2+ reopened at the
   imported starting location no matter where play ended. → Prep now supplies only
   the WHAT of session 1 (activity, cast, clues); WHERE comes from the canonical
   resolution below.
4. **No canonical anchors.** "Starting location" and "where the party is" were the
   same guess. → Four explicit fields with one meaning each (see schema).
5. **Whole-scene teleportation.** Travel moved every scene participant even when
   their canonical position was elsewhere. → Only characters co-located with the
   actor travel along; a left-behind character stays.
6. **Session Zero didn't create a world.** Table profile only; owners without a
   ready campaign file hit the tavern. → `!rv campaign create <premise>`.
7. **No deploy verification.** Playtest behavior couldn't be matched to a build.
   → `!rv diagnostics`.
8. **No money.** Purchases were pure narration. → Wallets + ledger (foundation).

## The canonical opening resolution

`SessionOpeningService.resolve_opening_location` (single implementation; the
Discord bridge delegates):

1. `campaign.current_party_anchor_id` — continuity: where play last was.
2. Attending characters' canonical `location_id` (majority vote).
3. `campaign.starting_location_id` — imported / AI-approved / owner-set.
4. Legacy `session_prep.opening_location_id` (pre-E7 imports; backfilled by the
   migration).
5. The campaign's **only** location (by count, never by recency).
6. Otherwise `None` → the bridge shows a setup-incomplete notice listing
   `campaign create` / `campaign import` / `session start at <name>`.
   **Nothing is ever invented.**

Anchor updates: session opening sets the anchor; `TravelService` moves it with the
active scene. `!rv session start at <ชื่อสถานที่>` lets the owner pin it (and seeds
`starting_location_id` on first use).

## AI-assisted campaign creation

`!rv campaign create <one-paragraph premise>` (owner only) runs the
`propose_campaign_world` job (`app/ai/jobs/campaign_creator.py`), which emits the
**same `CampaignProposal` schema as the file importer** — one validation, one
review card, one approve/reject lifecycle (`!rv campaign import approve|reject
<id>`). The AI proposes; deterministic validation checks keys/references/graph;
the owner approves; only then does the world commit. Committed rows carry
provenance `AI_PROPOSED_CANON` (locations and canon records), so explicit imported
canon (`IMPORTED` / `IMPORTED_CANON`) remains distinguishable and outranks it.

One semantic retry feeds validation errors back to the model; a second failure
surfaces to the owner instead of committing a broken world.

## Schema changes + migrations

- `campaigns`: `starting_location_id`, `current_party_anchor_id` (String(32),
  nullable, no FK to avoid a table cycle), `default_session_opening` (Text),
  `world_model_version` (Int, default 2).
  Migration `20260711_campaign_anchors` **backfills** `starting_location_id` from
  `session_prep.opening_location_id` for existing imported campaigns.
- `wallets` (one per character) + `currency_transactions` (signed-amount ledger
  with `transaction_type`, `idempotency_key` unique, `source_event_id`).
  Migration `20260711_economy`.
- `characters.planned_subclass` (from the character-options WIP this slice
  finished): NOTE — no migration existed for it; SQLite dev DBs created via
  `create_all` are fine, but a production Postgres deploy needs
  `ALTER TABLE characters ADD COLUMN planned_subclass VARCHAR(80)` folded into the
  next migration.

Deploy: `alembic upgrade head`, restart, then run `!rv diagnostics` in an owner
channel and confirm `git`, `db migration head`, and `content` hash match the build
you tested.

## Money (§15 foundation)

`WalletService` is the only mutator: signed amounts, atomic apply inside the
caller's unit-of-work, refuses accidental negative balances (explicit
`allow_debt` for agreed loans), idempotency keys make Discord retries commit at
most once, every change writes a `CurrencyTransaction`. Both character-creation
paths grant class-appropriate starting funds (idempotent per character).
`!rv wallet` shows balances + recent ledger lines. Shops/pricing/purchase flow are
the next milestone — narration-driven purchases must call this service.

## Time (§14 visibility)

`format_game_time_th` → `วันที่ 3 · 17:40 · เย็น` (engine-owned day segment).
Shown in: session-title footer, travel arrival frames (with `เดินทาง N นาที`),
and the new `!rv time` (clock + party anchor + session state).

## Diagnostics (§23)

`!rv diagnostics` (owner only): git SHA (env `REVERIE_GIT_SHA` or local git),
process start time, alembic head, LLM provider/model, prompt/importer/memory
versions, engine vs campaign world-model version, ruleset id + rules-content hash
+ registry counts. Never secrets. Version stamps live in `app/core/versions.py`.

## New/updated tests (178 green)

- `tests/test_campaign_agnostic.py` (12): setup-incomplete notice commits nothing;
  create→review→approve→canon with provenance + starting location; reject commits
  nothing; session 2 opens at the anchor, not the start; `session start at`;
  only-location fallback; wallet grant/overspend/idempotency/transfer; `!rv time`;
  diagnostics owner-gate + no secrets.
- `tests/test_world_canon.py` (+2 asserts, +1 test): travel drags the anchor;
  arrival frame shows time; a left-behind character does not teleport.
- Journeys updated to the new story: onboarding hits the notice, creates a world
  from one idea, approves, then plays; acceptance journey creates its world via
  AI and opens session 1 at the approved start.
- `tests/test_character_options_expansion.py` fixed (real campaign/member rows,
  committed draft) and the creation walk now exercises the planned-subclass step.

## Known limitations (deliberate, documented)

- **Split parties**: one active scene per session is baked into the pipeline
  (10+ call sites). Travel no longer teleports absent characters, but a true
  split (two concurrent scenes, per-actor scene resolution) needs the multi-scene
  refactor — the single biggest remaining §18 item.
- Consequence delta allowlist is still narrow (`note/advance_time/
  raise_suspicion/reveal_secret/reveal_fragment`); the §12 typed-command catalog
  (CHANGE_RELATIONSHIP, TRANSFER_CURRENCY, …) should be built on the wallet/event
  foundations now in place.
- NPC memory is knowledge/attitude-based (`NPCFact`/`NPCRelationship`); the §10
  episodic memory + relationship dimensions + witness pipeline is not yet built.
- Freeform (heading-less) Markdown import, connective-geography inference (§4),
  shops (§15), backstory→mechanics proposals (§8/§16), and the Grimoire/DM-Studio
  surfacing of anchors/wallets/time are next milestones.

## Recommended next milestones

1. §12 typed consequence commands (relationship/currency/location-state) over the
   existing delta validator — makes play change the world.
2. §10 NPC episodic memory + retrieval into `NPCSocialService` — makes NPCs
   remember specific players.
3. §15 shops + natural-language purchases through `WalletService`.
4. §18 multi-scene sessions (split party).
5. §3 freeform semantic import (two-stage: extraction → the existing validator).
