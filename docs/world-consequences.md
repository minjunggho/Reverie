# Persistent World Consequences (§11–13)

Player actions must permanently affect the world. This slice adds the durable
consequence + knowledge layer the world lacked, built entirely on the existing
substrate — no parallel engine.

## Product law

A committed action changes the world and the world *remembers*. Consequences persist
across sessions and restart; delayed consequences fire on their own in-world schedule
**exactly once**; and information does **not** travel instantly — a deed is known only
where it has actually been perceived, reported, or gossiped to.

Nothing here invents authoritative mechanics. HP/damage, currency, items, and combat
stay owned by the dice/economy/inventory/combat services and are reused, never
re-implemented. What lives here is CONSEQUENCE and KNOWLEDGE.

## Witness resolution — `app/world/witness_service.py`

Four facts are kept separate: **event perceived**, **actor identified**, event
interpreted, event reported. `WitnessService.resolve(...)` models the spec's factors
(presence, hearing, lighting, concealment, disguise, invisibility, consciousness,
public/private space, nearby connected locations) as explicit conditions and returns a
`WitnessResolution`:

- present, conscious NPCs (not `physical_state == "dead"`) and living characters at the
  location witness by **sight**; a `loud` event also reaches NPCs/characters in
  **connected** locations by **hearing** (they perceive, they cannot identify);
- `event_perceptible = public or loud or not concealed` — a concealed act in a private,
  quiet place goes unnoticed (the hidden pickpocket);
- `attributable = not (disguised or invisible or concealed) and lit` — a disguised,
  invisible, or unseen actor is perceived **without** being identified.

`WitnessResolution.perpetrator_ref` is the actor **only if someone identified them** —
otherwise `None`, the core mechanism behind an unattributed (open) crime.

## Typed commands — `app/world/consequence_service.py`

`ConsequenceService` is the one validated command layer. Every command: validates its
args (schema), resolves its target and refuses cross-campaign references (isolation +
target validation), is idempotent where it creates rows (`source_event_id` /
`idempotency_key`), and records exactly one canonical `Event` through the shared
`EventService`.

| Command | Effect | Persists via |
| --- | --- | --- |
| `injure_npc` / `set_npc_available` / `move_npc` / `change_emotion` | durable NPC condition, availability, position, mood | `npcs.physical_state` / `available` (new) |
| `change_access_state` | sever/open a travel edge (destroyed bridge) | `location_connections.access_state` |
| `set_location_state` / `discover_route` | damaged/destroyed/closed places, revealed routes | `locations.state` / `discovery_state` |
| `update_quest` | quest state + progress + leads (upsert by key) | `quests` |
| `record_crime` / `discover_crime` / `report_crime` | perceived/identified/reported crimes | `crime_records` |
| `change_reputation` | standing within one social scope | `reputations` |
| `spread_rumor` / `widen_rumor` | (possibly false) info climbing the reach ladder | `rumors` |
| `create_faction` / `advance_faction` / `update_threat` | fronts/threats advancing on their timeline | `factions` / `threats` |
| `schedule_response` | a delayed consequence, deduped | `scheduled_world_events` |
| `create_memory` | episodic NPC memory of a character (delegates) | `NPCMemoryService` |

**Scopes** (`REPUTATION_SCOPES`): Individual, Local, Settlement, Faction, Region,
Profession, Underworld, Religious, Political. A person can be a local hero and wanted
by the underworld at once — distinct rows keyed by `(subject, scope, scope_ref)`.

## Information spread — not instant

News reaches the world through witnesses (perceivers on the crime record), reports
(`report_crime`), gossip/rumors (`spread_rumor` → scheduled `rumor_spread` →
`widen_rumor`), faction networks (`advance_faction` growing `knowledge`), and time. A
rumor climbs `LOCAL → SETTLEMENT → REGION → POLITICAL` one rung per spread event, so a
district over learns of it *later*, never at once.

## Delayed consequences — exactly once

`schedule_response` persists a `ScheduledWorldEvent`. `WorldClockService.advance_time`
already ticks due threats/events on the single authoritative time path; the fired-event
loop now also **dispatches** recognised consequence kinds — `guard_response`,
`rumor_spread`, `faction_action`, `threat_action`, `npc_availability` — back through
`ConsequenceService`. The `resolved` flag guarantees a fired event is never selected
again, and `schedule_response` dedupes on `idempotency_key`, so the same trigger can
neither schedule nor apply a duplicate. Unknown kinds still fire as plain markers
(back-compat).

Examples that "just work": guards arrive in ten minutes; a merchant reports theft in
the evening; a rumor reaches another district later; a faction sends agents after two
days; an injured shopkeeper closes tomorrow; a bridge's repair begins after several
days.

## AI-proposable subset

The `ConsequencePlanner` may only PROPOSE a curated, non-mechanical subset through the
existing `DeltaApplier` allowlist — `spread_rumor`, `update_quest`, `change_reputation`
— each delegating to `ConsequenceService`. Injury, access-state, crime attribution,
faction advancement, currency/items/combat, and scheduling stay engine-owned off the
proposal path, consistent with "the model may time a consequence, never invent
authoritative numbers."

## Schema — migration `20260721_consequences`

New tables `factions`, `reputations`, `crime_records`, `quests`, `rumors` (crime/rumor
carry an indexed `source_event_id` for idempotency); new NOT-NULL `npcs.physical_state`
(`'healthy'`) and `npcs.available` (`1`) with safe defaults so every existing NPC is
unchanged. `tests/test_migrations.py` proves the migrated schema is structurally
identical to `Base.metadata.create_all()` and that the revision downgrades and
re-upgrades cleanly.

## Tests — `tests/test_world_consequences.py`

The nine required acceptance cases: public assault creates witnesses + a guard
response; hidden theft stays unknown until discovered; disguise prevents identity
attribution; a rumor spreads over time; a faction responds later; a destroyed bridge
changes navigation after restart; NPC injury persists; quest changes persist; a
scheduled event triggers exactly once.
