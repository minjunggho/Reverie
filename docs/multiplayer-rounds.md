# Multiplayer rounds: the shared decision window

The unit of resolution changed from **"one player message = one resolved world turn"**
to **"one shared decision window produces one frozen set of intentions, which the engine
resolves into one coherent world update."**

This document covers the **engine core** (phase 1, implemented). The Discord decorated
planning panel and the web/WebSocket real-time layer are phases 2–3 (contracts below).

## Why the old flow blocked coordination

Committed actions were serialized by a per-session `asyncio.Lock`
([serializer.py](../backend/app/orchestration/serializer.py)): the second `!` blocked
until the first fully committed and narrated. Arrival order *was* turn order, so players
could not plan together, could not revise once processing began, and each message became
an isolated scene. The lock is still correct for *legacy* one-by-one campaigns, but it is
no longer the unit of play when planning mode is on.

## State machine

`DecisionWindow.phase` ([enums.py](../backend/app/models/enums.py) `WindowPhase`):

```
AWAITING_ACTIONS ──(all required ready / host force)──► READY_TO_RESOLVE
      ▲  │                                                      │
      │  └─(edit unsets ready)                                  ▼
 (host reopen)◄──────────────────────────────────────────  RESOLVING
                                                               │
                                                               ▼
                                        PRESENTING_RESULTS ──► ROUND_COMPLETE
```

`VALIDATING` and `AWAITING_ROLLS` are reserved for the manual-dice sub-flow (phase with
outstanding player rolls). `CANCELLED` is a host terminal state. Behaviour is driven by
this column, **never** by chat-message order.

## Schema (migration `20260728_decision_windows`)

- **`decision_windows`** — one per `(scene_id, round_id)` (unique). Holds `phase`,
  `mode` (COMBAT/NONCOMBAT), `required_actor_ids`, `excused_actor_ids`, a `config`
  policy snapshot taken at open time, the immutable `frozen_snapshot`, the resolved
  `round_package`, `resolved`, and an optimistic `version`.
- **`action_submissions`** — one per `(window_id, actor_id)` (unique → submit/edit is an
  upsert, never duplicate rows). Structured fields (`dialogue`, `movement_intent`,
  `destination`, `primary_action`, `action_target`, `bonus_action`/`bonus_target`,
  `interaction`, `reaction_intent`, `condition`, `fallback_action`/`fallback_target`,
  `desired_tone`, `declared_resource_use`, `required_rolls`), plus `raw_player_text`
  (preserved verbatim for narration), `revision`, `ready_at`, `passed`, `visibility`,
  `validation_status`/`validation_errors`, and `idempotency_key`.

## Server-authoritative guarantees

Implemented in [DecisionWindowService](../backend/app/rounds/service.py); every one has a test in
[test_decision_window.py](../backend/tests/test_decision_window.py):

| Guarantee | Mechanism |
|---|---|
| Ready locks only the current revision | `mark_ready(revision=…)` rejects a stale revision (`ConflictError`) |
| Editing unsets Ready | `submit` bumps `revision`, clears `ready_at` |
| A stale client can't overwrite a newer revision | `submit(expected_revision=…)` → `ConflictError` |
| Duplicate submissions don't duplicate | unique `(window_id, actor_id)` + `idempotency_key` no-op |
| Ready updates are read from the server | `all_required_ready` computes from rows |
| Resolution never begins from client state | `freeze` re-checks readiness from the DB |
| Once resolving, actions can't change | `_require_open` blocks writes outside `AWAITING_ACTIONS` |
| Atomic snapshot persisted for replay | `frozen_snapshot` written once; `round_package` stored |
| Resolve twice is safe | `resolved` guard returns the stored package |

## Policies (configured, not scattered)

[WindowPolicies](../backend/app/rounds/policies.py) from `campaign.config["planning"]`. Defaults reflect
the product decisions:

- `enabled: "auto"` — a shared window governs the scene when **2+ players** are eligible;
  a solo player keeps the fast path. (`"always"` / `"off"` also available; `"off"` keeps
  the legacy one-by-one flow entirely — the **backward-compatibility switch**.)
- `single_player_auto_ready: true` — solo submit auto-readies and resolves immediately.
- `countdown_seconds: 0` — wait for everyone; countdown is opt-in.
- `trivial_invalidation: "fallback_or_skip"` — an invalidated action uses its declared
  fallback or is safely skipped; a *major* invalidation can `"pause"` for a decision.

## Resolution & the round package

[RoundResolver](../backend/app/rounds/resolver.py) freezes the window, orders the actions, applies them,
and emits a [`RoundPackage`](../backend/app/rounds/resolver.py) — the structured hand-off to a **single**
combined narration and the replayable record:

- **Combat** — ordered by **verified initiative** (`Combatant.initiative`). An earlier
  kill invalidates a later action against the dead target; a declared `fallback_target`
  is used instead, else the action is skipped and recorded in `invalidated` (never
  silently replaced). Attacks resolve through the real engine (`CombatService`), and
  every die/total/hit/damage is captured.
- **Noncombat** — [classified](../backend/app/rounds/classifier.py) as cooperative / independent /
  conflicting / sequential / mutually-exclusive / secret / interrupting / social-overlap,
  and ordered dependency-first (a distraction resolves before the theft it enables).

The narrator receives one `ROUND_PACKAGE` message (order, relationships, each player's
**own words + dialogue**, resolved verdicts, invalidations, fallbacks) under
`ROUND_NARRATOR_SYSTEM`, and returns one connected scene — not isolated paragraphs.

## Live Discord wiring (implemented)

The bridge ([bridge.py](../backend/app/discord_bridge/bridge.py)) routes real play through the window system —
this is now the live flow for both single- and multiplayer, not just the engine core.

- **Auto-detection.** `opening_service` opens a window for **1+ eligible actors**
  (`WindowPolicies.should_open_window`), so single-player and multiplayer use the same
  system; only `planning.enabled = "off"` keeps the legacy one-by-one path.
- **Submission / edit.** A `! …` message with an active window routes to
  `_route_window_submission`: it upserts the player's intention (edit bumps revision and
  clears Ready), idempotent on the Discord message id. Multiplayer waits; it never
  resolves on first submit.
- **Controls → buttons.** `decision_window_screen` / `cinematic_scene_screen` render real
  buttons whose opaque values (`~rv-ready|unready|pass|force|reopen:<window>`) re-enter
  the bridge via the client's `_make_on_choice` (identity from `interaction.user`, never
  the message). `_parse_window_control` → `_route_window_control` applies them
  server-authoritatively.
- **Ready gate.** Resolution runs only when `all_required_ready` is true (computed from
  rows), or a host forces it.
- **Host controls.** `~rv-force` (force-resolve now) and `~rv-reopen` (return to planning,
  clearing Ready) are **owner-only** — `_is_host` checks `CampaignMember.role == OWNER`
  from the DB, never trusts the client. A non-host press gets a notice; nothing mutates.
- **Single-player depth.** A one-actor window auto-readies on submit and resolves through
  the **full committed pipeline** (`_resolve_solo_via_pipeline`) — spell slots, saving
  throws, travel, item transfer, and the dice ritual all keep full depth, never the shared
  resolver's coordination-only intent recording. The slot is then consumed and the next
  window opens. Multiplayer rounds use the `RoundResolver` (combat + coordination).
- **Round chaining.** After any resolution the next window (`round_id + 1`) opens for the
  scene, so play is a continuous sequence of shared rounds.

Covered by [test_storytelling_pipeline_v2.py](../backend/tests/test_storytelling_pipeline_v2.py): two-player collect/edit/wait/
resolve, solo auto-ready at pipeline depth, single-player-by-default, host force-resolve,
host reopen, and player-cannot-use-host-controls.

## Backward compatibility & migration

- **Additive only.** Two new tables; nothing existing changes until a window opens.
- **Legacy escape hatch.** `planning.enabled = "off"` restores the pure one-by-one
  bridge → serializer → pipeline path.

## Phase 3 contract (web real-time, not yet implemented)

The activity API is currently poll-only. Add `GET /campaigns/{id}/round` (returns
`DecisionWindowService.panel`), `POST …/round/submit|ready|pass`, and a
`WS …/round/stream` broadcasting `{type: "window.updated", panel}` on every mutation. The
server stays authoritative; the socket is a notification, never the source of truth.

## Deferred to later phases (with the hooks already in place)

Manual-dice player-roll collection (`AWAITING_ROLLS` + `required_rolls`), multi-target
saving-throw rounds, NPC/enemy intentions in the resolver, reaction/interrupt player
decisions mid-resolution, and reconnection replay. Each has a schema field or enum state
reserved for it; none require another migration.
