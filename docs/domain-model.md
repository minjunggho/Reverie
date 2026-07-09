# Domain Model — Reverie

Legend: **C** = canonical (source of truth) · **W** = mostly working state,
distilled to events on transition · **D** = derived/prose (never canonical).

All primary keys are string UUIDs (`uuid4().hex`) for dialect portability.
"JSON" columns are portable (JSONB on Postgres, JSON-as-text on SQLite).

## MVP entities (built)

### User — **C**
A Discord user. `id`, `discord_user_id` (unique), `display_name`.

### Campaign — **C**
A game world instance bound to a Discord guild/channel.
`id`, `discord_guild_id`, `game_channel_id` (unique), `owner_user_id`, `name`,
`config` JSON (tone, assistance defaults, lethality, failure_progress_level,
rules-subset flags), `current_game_time` (int minutes since campaign epoch),
`status` (CampaignStatus).

### CampaignMember — **C**
A User's membership in a Campaign.
`id`, `campaign_id`, `user_id`, `role` (OWNER/PLAYER), `active_character_id`.
Unique(`campaign_id`, `user_id`). **One active character per member (MVP).**

### Character — **C** (all mechanical values engine-authoritative)
`id`, `campaign_id`, `owner_member_id`, `name`, `ancestry`, `char_class`
(from supported subset), ability scores (`str/dex/con/int/wis/cha`),
`proficiencies` JSON (skill list), `proficiency_bonus`, `hp`, `max_hp`, `ac`,
`level`, `xp`, `conditions` JSON list, `resources` JSON.

### Session — **C**
`id`, `campaign_id`, `number`, `status` (SessionStatus lifecycle),
`active_play_state` (ActivePlayState), `started_at`, `ended_at`,
`attendance` JSON (member ids), `version` (optimistic lock).

### Scene — **W** (distilled to events on transition)
`id`, `session_id`, `location_id`, `mode` (EXPLORATION/SOCIAL/DOWNTIME/COMBAT),
`purpose`, `dramatic_question`, `tension` (int), `participants` JSON,
`visible_entity_ids` JSON, `relevant_object_ids` JSON, `immediate_threat_ids` JSON,
`pending_action_id`, `scene_start_game_time`, `status`, `version`.

### Event — **C** (append-only history of record)
See `event-model.md`. Fields: `id`, `campaign_id`, `session_id`, `scene_id`,
`event_type`, `campaign_time`, `real_time`, `actor_entity`, `target_entities` JSON,
`location_id`, `witnesses` JSON, `visibility` (Visibility), `payload` JSON,
`mechanical_changes` JSON, `narrative_significance` (int), `seq` (monotonic).

### Location — **C**
`id`, `campaign_id`, `name`, `description_obvious`, `description_focused`,
`description_hidden`, `connections` JSON, `contents` JSON, `state` JSON.

### NPC — **C**
`id`, `campaign_id`, `name`, `personality`, `voice_register`, `goals` JSON,
`current_location_id`, `attitudes` JSON (per-entity), `emotional_state`.

### NPCKnowledge / NPCBelief / NPCSuspicion / NPCMemory / NPCRelationship — **C** (DM-scoped)
Epistemic + relational records. Each row: `npc_id`, subject/fact, `status`
(KNOWS/BELIEVES/SUSPECTS/HEARD_RUMOR/FORGOTTEN/UNAWARE), `source`, `confidence`.
Implemented in Phase 11.

### ItemDefinition — **C** / InventoryEntry — **C** (ledger)
`ItemDefinition`: reusable template (`id`, `campaign_id?`, `name`, `kind`, `data`).
`InventoryEntry`: `character_id`, `item_definition_id`, `quantity`, `equipped`.

### Quest — **C**
`id`, `campaign_id`, `title`, `status`, `steps` JSON, `giver_npc_id`, `deadline_game_time`.

### KnowledgeRecord — **C**
A fact + provenance (who observed / was told / heard rumor / believes / suspects).
`id`, `campaign_id`, `fact`, `truth_value`, `visibility`, `provenance` JSON.

### Secret — **C** (DM-scoped)
A true fact hidden from some/all players. `id`, `campaign_id`, `fact`,
`visibility_map` JSON. Retrieval layer guarantees it never reaches player output.

### Threat — **C**
`id`, `campaign_id`, `name`, `goal`, `status`, `progress` (0..100),
`next_action`, `scheduled_game_time`. Phase 12.

### ScheduledWorldEvent — **C**
`id`, `campaign_id`, `due_game_time`, `kind`, `payload`, `resolved`. Phase 12.

### CombatEncounter — **W** / Combatant — **C-enough**
Basic combat state; survives a mid-combat crash. Phase 13.

### ProcessedMessage — **C** (operational idempotency)
`id`, `discord_message_id` (unique), `campaign_id`, `session_id`, `stage`
(ProcessingStage), `category`, `pending_action_id`, `result` JSON,
`created_at`, `updated_at`.

## Future / non-MVP (scaffolded only if trivially useful)
Faction hierarchies beyond Threat; multi-character-per-member; XP-vs-milestone
toggles beyond a flag; downtime crafting.

## Enum summary
- `CampaignStatus`: SETUP, ACTIVE, PAUSED, ARCHIVED
- `SessionStatus`: PREPARATION, OPENING, ACTIVE_PLAY, CLOSING, POST_SESSION, COMPLETE
- `ActivePlayState`: SCENE_FRAMING, TABLE_OPEN, CLARIFICATION_REQUIRED, ADJUDICATING,
  RESOLVING, COMMITTING_STATE, NARRATING, SCENE_TRANSITION, COMBAT_INITIALIZING,
  COMBAT_ACTIVE, COMBAT_RESOLVING_TURN
- `MemberRole`: OWNER, PLAYER
- `SceneMode`: EXPLORATION, SOCIAL, DOWNTIME, COMBAT
- `MessageCategory`: COMMITTED_ACTION, DM_QUESTION, RULES_QUESTION, CHARACTER_DIALOGUE,
  OOC_DISCUSSION, SOCIAL_OR_JOKE, UNKNOWN
- `CommitmentSource`: EXPLICIT_PREFIX (impl) · AI_INFERRED, DISCORD_BUTTON, VOICE_CONFIRMED (reserved)
- `Visibility`: PUBLIC, PARTY, PLAYER_ONLY, DM_ONLY, NPC_SCOPED
- `KnowledgeStatus`: KNOWS, BELIEVES, SUSPECTS, HEARD_RUMOR, FORGOTTEN, UNAWARE, UNAWARE
- `ProcessingStage`: RECEIVED, INTERPRETED, ADJUDICATED, RESOLVED, COMMITTED, NARRATED, SENT, FAILED
- `ResolutionType`: AUTOMATIC_SUCCESS, AUTOMATIC_FAILURE, ABILITY_CHECK, SAVING_THROW, ATTACK, SUPPORTED_SPECIAL_RESOLUTION
- `ConsequenceClass`: SUCCESS, SUCCESS_WITH_COST, FAILURE, FAILURE_WITH_CONSEQUENCE, FAILURE_WITH_PROGRESS
