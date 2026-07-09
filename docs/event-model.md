# Event Model

The Event log is the **canonical append-only history** of meaningful game
occurrences. It is not a chat log. Generated prose is derived from events; events
are never derived from prose.

## Fields
| Field | Meaning |
|---|---|
| `id` | uuid |
| `seq` | monotonic integer per campaign (ordering / replay) |
| `campaign_id` | FK |
| `session_id` | FK (nullable for campaign-level events) |
| `scene_id` | FK (nullable) |
| `event_type` | from the taxonomy below |
| `campaign_time` | in-world time (int minutes since campaign epoch) |
| `real_time` | wall-clock UTC |
| `actor_entity` | entity ref string, e.g. `character:<id>`, `npc:<id>`, `system` |
| `target_entities` | JSON list of entity refs |
| `location_id` | FK (nullable) |
| `witnesses` | JSON list of entity refs — seeds information provenance |
| `visibility` | PUBLIC / PARTY / PLAYER_ONLY / DM_ONLY / NPC_SCOPED |
| `payload` | JSON — event-type-specific structured data |
| `mechanical_changes` | JSON — e.g. `{"hp": {"from":21,"to":13}}` |
| `narrative_significance` | int 0..100 — ranks retrieval/recap importance |

## Taxonomy (MVP)
`SESSION_STARTED`, `SESSION_ENDED`, `SCENE_STARTED`, `PLAYER_ACTION_COMMITTED`,
`ABILITY_CHECK_RESOLVED`, `ATTACK_RESOLVED`, `DAMAGE_APPLIED`, `ITEM_GAINED`,
`ITEM_LOST`, `CHARACTER_MOVED`, `NPC_STATE_CHANGED`, `KNOWLEDGE_GAINED`,
`QUEST_STATE_CHANGED`, `WORLD_TIME_ADVANCED`, `THREAT_ADVANCED`, `COMBAT_STARTED`,
`COMBAT_ENDED`.

## Event vs transient scene context
- An **event** is a meaningful occurrence with lasting significance (a check was
  resolved, HP changed, an item moved, an NPC learned something, time advanced).
- Transient scene chatter, questions, jokes, and intermediate working state are
  **not** events. We do not emit an event per Discord message.
- `witnesses` on an event is the raw material for who *could* later know a fact
  (see `knowledge` / provenance).

## Atomicity rule
Every canonical state mutation is committed **in the same transaction** as the
Event(s) that record it (`app/db/unit_of_work`). If the state write or the event
write fails, both roll back. Tests prove this in Phase 3.

## Visibility rule
`visibility` on an event, combined with `witnesses`, is what the retrieval layer
uses to decide whether an event may enter a given AI context. A `DM_ONLY` event
can physically never reach the player-safe recap builder.
