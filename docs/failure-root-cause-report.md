# Playtest Failure Root-Cause Report

Grounded in traced production code (file:line references verified 2026-07-15), not
speculation. Confidence is marked per item: **PROVEN** (traced to code), **STRONG**
(code supports it; one live repro missing), **AUDIT** (needs live capture).

A cross-cutting cause first, because it feeds many items below:

> **C0 — Branch fragmentation.** The NPC-intelligence + persistent-consequence layer
> (witnesses, crime, factions, rumors, NPC injury/availability) lives on
> `reverie/world-consequences`; the cinematic prologue + belief-flow fixes live on
> `reverie/cinematic-opening`; class framework on a third. **No playtest build contains
> all of them.** Several "missing" behaviors (NPC persistence, reactions) are built but
> not on the branch that was played. Correction: unify onto one integration branch
> before further feature work. Risk of recurrence: certain until merged.

---

## F1 — Active campaigns randomly return to the opening scene — PROVEN

- **Symptom:** party or one player suddenly back at the campaign start; late joiner
  spawns at the beginning; players separated.
- **Repro:** (a) `!rv session start at <loc>` mid-campaign; (b) end session, damage the
  anchor (e.g. re-import replaces location rows), `!rv session start`; (c) start a new
  session while one character is at a different location.
- **Code path:** `admin_bridge.py:_session_start` → `SessionOpeningService.
  resolve_opening_location` (opening_service.py) → `open_new_session` step 4.
- **Immediate causes (three, compounding):**
  1. `resolve_opening_location` falls back anchor → positions → **`campaign.
     starting_location_id`** → legacy prep → only-location. For an ONGOING campaign
     with a dangling anchor and unset character positions this silently resolves to
     the campaign start — literally `saved_location or campaign.start_location`.
  2. `open_new_session` **places every attending character at the resolved location**
     (`char.location_id = location_id` for all participants) — mass teleport whenever
     resolution is wrong, and it separates a split party by force-merging it.
  3. `!rv session start at <loc>` **overwrites `current_party_anchor_id`**
     (admin_bridge.py:767) — an unlabelled teleport command.
- **Deeper cause:** "where does play open" and "where is each character" are conflated;
  fallbacks designed for a *brand-new* campaign run for *ongoing* ones; error recovery
  by repositioning instead of by integrity failure.
- **Affected systems:** session opening, anchors, character position, late join.
- **Recurrence risk:** high — every session start rolled the dice.
- **Correction (this commit series):** start-location fallbacks are legal only for a
  campaign with **no prior sessions**; an ongoing campaign whose anchor/positions are
  all dangling now raises `StateIntegrityError` (nothing moves, no session created,
  exact missing state reported); positioned characters are **never moved** on session
  open (only characters with no live position are placed — the late joiner joins the
  party anchor); `start at` remains as the explicit, owner-chosen repair.
- **Migration impact:** none (behavioral; no schema change).
- **Regression tests:** `tests/test_session_continuity.py` (dangling-anchor integrity
  error; positioned characters not teleported; late joiner placed at anchor not start).
- **Playtest validation:** multi-hour session with restarts + late join; nobody returns
  to the opening without an explicit owner command.

## F2 — Party members lose one another — STRONG

- **Symptom:** teammates in the same room can't perceive/hand items/follow each other.
- **Code path:** `Scene.participants`/`visible_entity_ids` are set at scene creation
  (`opening_service.py` step 4, `scene_service`), and `Character.location_id` is
  canonical position — but the interpreter/clarification layer does not consult scene
  membership before asking "which teammate / how will you find them"; PC↔PC
  interactions have no authoritative presence check, and F1's mass-placement corrupts
  positions underneath the scene.
- **Deeper cause:** no enforced invariant "same session+scene+location ⇒ mutually
  interactable"; presence inferred per-request instead of read from scene state.
- **Correction (next slice):** authoritative scene-membership read (`participants` +
  positions) consulted before ANY clarification; a mismatch between scene membership
  and `location_id` reports a state-integrity failure instead of inventing distance.
  F1's fix removes the biggest corrupter. Regression: the "party remains together"
  acceptance script. Migration: none.

## F3 — Following a visible teammate asks for a direction — PROVEN (mechanism exists, entry path broken)

- **Code path:** the follow mechanic EXISTS and is transactional:
  `travel_service.py:138-147` moves `PositionService.consenting_followers` with the
  leader and breaks stale follows. `Character.following_character_id` is persisted.
- **Immediate cause:** the *interpretation* path for "ตาม X ไป / I follow her" doesn't
  resolve a person as a destination: movement interpretation requires a location or
  direction, and `LocationResolver` resolves NPC-directed references but the
  follow-a-PC intent (establishing `following_character_id`) is not wired from
  natural-language input — so the pipeline asks "which direction?"
- **Deeper cause:** follower relationship treated as consent/config, not as a
  first-class movement intent.
- **Correction (next slice):** interpreter maps follow-intent verbs + person reference
  (scene member by name/alias/pronoun) → establish follow + move-with-target in one
  transaction; clarification allowed only per the ambiguity policy (multiple matches,
  target truly absent/unknown). Regression: the "following works contextually" script.

## F4 — Canonical locations inconsistent or duplicated — STRONG

- **Code path:** `Location` already has `name_th/name_en/aliases` and one
  authoritative `LocationResolver`; the travel graph refuses unknown places. The gaps:
  (a) import (`canon_import.py`) builds `by_key` from author keys with NO duplicate/
  alias-collision detection; (b) `_slug()` (canon_import.py:183) strips ALL non-ASCII,
  so Thai names slug to junk — Thai prose references can silently mismatch keys;
  (c) `expansion_service` can create a near-duplicate of an existing place if the
  resolver misses an alias.
- **Correction:** import-time validation (duplicate keys, alias collisions, unmatched
  parents — extend `graph_validation`), Thai-safe normalization for all key/name
  matching, and resolver-first lookup before any expansion. Migration: none (data
  audit tool advisable). Regression: alias-resolution acceptance script.

## F5 — Session cannot start without manually specifying a location — PROVEN

- **Code path:** `canon_import.py:577` sets `campaign.starting_location_id` **only on
  exact slug match** `proposal.starting_location in by_key`; the preview merely warns
  (line 354). `_slug()` destroys Thai text, so a Thai `opening location:` line in the
  imported file can NEVER match. Then `admin_bridge._session_start` → resolution finds
  nothing → "setup incomplete", demanding `start at`.
- **Correction (this commit series):** at approval, when the slug doesn't match a key,
  resolve the RAW prose opening location against imported locations' names/aliases
  (Thai-safe casefold matching) before giving up; an unresolved start remains a loud
  warning. Migration: none. Regression: import-with-prose-Thai-start test.

## F6 — Manual `start at` bypasses the cinematic opening — PROVEN

- **Code path:** `opening_service.open_new_session` gates the prologue on
  `number == 1` (and a known main goal). The playtest consumed session 1 with broken
  starts/restarts (F15), so every later session was `number > 1` → cinematic never
  plays again. `start at` itself doesn't skip the prologue — session numbering does.
- **Deeper cause:** "cinematic played" inferred from session number instead of state.
- **Correction (this commit series):** explicit persisted flag
  `campaign.config["opening_cinematic_played"]`; the prologue plays whenever the flag
  is unset and canon provides a main goal — session 1 or 5, inferred or `start at` —
  and is set exactly once when rendered; resume/later sessions never replay. (Known
  edge: campaigns from before this change that already saw the prologue may replay it
  once; acceptable pre-release.) Migration: none (JSON config). Regression:
  cinematic-exactly-once tests incl. via-`start at` and after-restart.

## F7 — Narrated NPCs/creatures are not stored as entities — STRONG

- **Code path:** narration (`Narration` output) is free prose; nothing enforces that a
  described "infected woman" exists as an `NPC` row. The consequence/witness layer
  (branch `reverie/world-consequences`) only tracks committed entities. Scene
  `visible_entity_ids` holds refs, but the narrator can mention non-entities.
- **Deeper cause:** no pre-narration entity-commitment contract.
- **Correction (later slice):** narration validation pass — interactable entities named
  in narration must resolve to committed rows (create-then-narrate), else the
  narration is rejected/regenerated; scene framing exposes only committed entities.

## F8 / F9 — Forgets recent facts; excessive clarification — STRONG

- **Code path:** clarification issuance in the pipeline/interpreter doesn't first
  inspect scene membership, recent committed events (`EventService.list_events`),
  follow state, or inventory before asking. The data exists (events, NPC memory,
  scene state) — the ASK decision doesn't consult it.
- **Correction:** a clarification gate: before any question, resolve against (1) scene
  entities, (2) last N committed events, (3) conversation target, (4) follow/ownership
  state; ask only for genuine, materially different interpretations. Regression:
  "context does not disappear" script.

## F10 — Players repeat actions — STRONG

- **Cause:** responses lacking the outcome contract (attempt/roll/outcome/state
  change); `ProcessedMessage.stage` tracks resolution but narration can be vague, and
  identical re-attempts aren't recognized as repeats.
- **Correction:** enforce the response contract in the presenter; detect repeated
  identical attempts against recent events and answer with what changed / why nothing
  will.

## F11 — Inventory transfer consistency — PROVEN (capability missing)

- **Code path:** `InventoryService` has **no transfer operation at all** (no
  transfer/give method exists); items live as `InventoryEntry` rows. Any narrated
  hand-over therefore has NO state backing — narration can claim a transfer that never
  happened, and repeats can duplicate acquisitions.
- **Correction (next slice):** transactional `transfer_item(sender, receiver, item,
  idempotency_key)` with possession/presence validation + canonical event, reusing the
  wallet/`CurrencyTransaction` pattern; narration may only claim committed transfers.

## F12 — Duplicate/fragmented responses — PARTIALLY PROVEN / AUDIT

- **Code path:** inbound dedup EXISTS: `ProcessedMessage.discord_message_id` is
  `unique=True` and processing is stage-tracked with recovery
  (`bridge.py:_recover_committed_failure`); committed actions serialize per session
  (`bridge.py:198-203`). Multi-message output is partly by design (kinded messages).
- **Remaining risk:** interaction (button) clicks and any second listener/worker are
  outside `discord_message_id` dedup — needs a live audit of the Discord adapter; the
  in-process serializer does not serialize across processes.
- **Correction:** idempotency key extended to interaction ids; single-claim via the
  existing unique row; coalesce presentation into one coordinated response per action.

## F13 — Concurrent actions overwrite one another — PARTIALLY PROVEN

- **Code path:** per-session in-process queue (bridge serializer); optimistic
  versioning exists on drafts (`draft_store.save_draft` compare-and-update), sessions
  and scenes (`version` columns, `services/concurrency.py`). Gap: multi-process
  deployments share no queue, and not every scene/character write path asserts a
  version.
- **Correction:** version-assert on scene/character mutations in the committed
  pipeline; document single-writer-per-session as a deployment invariant until a DB
  claim queue exists.

## F14 — Campaign import/activation confusing — STRONG

- **Code path:** `canon_import` has PENDING→APPROVED but no versioned "active
  campaign version" concept; `campaign new` vs import interplay is implicit;
  activation == approval side-effects.
- **Correction (later slice):** explicit draft→validated→approved→ACTIVE states +
  status card (title/version/start/warnings) after import; block session start on
  ambiguous/inactive versions.

## F15 — Session restart as repair — PROVEN (command gap)

- **Code path:** only `session start` / `session end` exist (`admin_bridge.py:733`).
  No resume/pause/rollback — so players used end+start, which (pre-F1-fix) re-ran
  location resolution and could teleport. F1's fix makes start-after-end safe; full
  command separation (resume/pause/rollback/reset) is a later slice.

## F16 — No player orientation — STRONG

- **Cause:** scene state (location, participants, exits, goal, time) exists but no
  player-facing dashboard/refreshable summary surfaces it.
- **Correction (later slice):** `!rv where`-style scene card fed from authoritative
  scene state + main_story objective.

## F17 — DM doesn't guide — STRONG

- **Cause:** `main_story` (goals/leads/deadlines) exists and is maintained, but scene
  framing doesn't consistently inject the current objective/leads; no per-scene
  purpose/tension contract enforced.
- **Correction:** scene-framing contract pulls objective + at least one live lead into
  every EXPLORATION/SOCIAL frame.

## F18 — NPCs passive/shallow — STRONG (mostly C0)

- **Cause:** the NPC decision layer (recognition/willingness/bias), 8-dim
  relationships, episodic memory, injuries/availability, witnesses, and off-screen
  faction/threat advancement are BUILT — on `reverie/world-consequences` /
  NPC-intelligence commits — and absent from the played build (C0). Remaining real
  gaps: NPC initiative (interrupt/leave/pursue) and dialogue using memory
  consistently.
- **Correction:** merge C0; then an NPC-initiative pass driven by the existing
  decision layer + scheduled consequences.

## F19 — Generic narration/wording — STRONG

- **Cause:** prototype-era phrasing in fallback cards and error paths (e.g. generic
  diagnostics); prompts don't ban filler phrases; technical diagnostics leak wording
  meant for owners into player flow.
- **Correction:** audit pass over `_diagnostic`/notice strings + prompt style rules
  (ban listed filler; require in-world reasons for blocks).

## F20 — Frustrating loop — outcome of F1–F19

No separate mechanism; resolved by the order: continuity safety (F1,F5,F6 — this
series) → authoritative presence/follow/aliases (F2,F3,F4) → entities/inventory/
context (F7,F8,F11) → loops/duplication (F9,F10,F12,F13) → guidance/orientation/NPC
life (F16,F17,F18) → wording (F19).
