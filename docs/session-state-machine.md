# Session & Active-Play State Machine

## Campaign / Session lifecycle (practical, not linear-genre)
```
CAMPAIGN_SETUP -> SESSION_PREPARATION -> SESSION_OPENING -> ACTIVE_PLAY
              -> SESSION_CLOSING -> POST_SESSION -> SESSION_COMPLETE
```
EXPLORATION / SOCIAL / DOWNTIME are **scene modes** that occur in any order and
repeat. **COMBAT is structurally distinct** (initiative + formal turns) and is
entered/exited from within ACTIVE_PLAY. Nothing forces a linear
exploration→social→combat sequence.

`SessionStatus` maps: PREPARATION, OPENING, ACTIVE_PLAY, CLOSING, POST_SESSION,
COMPLETE.

## Active-play state machine (inside ACTIVE_PLAY)

`ActivePlayState` values and rules:

| State | Purpose | Valid input | Player perms | AI responsibility | Engine responsibility | Transitions |
|---|---|---|---|---|---|---|
| **SCENE_FRAMING** | present a new scene + decision point | (system) | read | frame scene in Thai | build scene, emit SCENE_STARTED | -> TABLE_OPEN |
| **TABLE_OPEN** (default rest) | normal table talk | any message | talk freely | classify + optionally answer | **mutate no state**; on `!` route to pipeline | -> ADJUDICATING (on `!`), -> CLARIFICATION_REQUIRED (interpreter needs info), stays on normal msg |
| **CLARIFICATION_REQUIRED** | hold a pending action | committing player's reply (others may still chat) | answer the question | ask ONE focused Thai question | persist pending action; no mutation until resolved/cancelled | -> ADJUDICATING (answered), -> TABLE_OPEN (cancelled) |
| **ADJUDICATING** | decide resolution + mechanic + DC | (system) | wait | RollNecessityJudge/MechanicSelector/DCResolver **propose** | clamp DC to bands, choose adv/dis | -> RESOLVING |
| **RESOLVING** | roll + compare | (system) | wait | none | authoritative dice, modifiers, outcome | -> COMMITTING_STATE |
| **COMMITTING_STATE** | write canonical deltas + events atomically | (system) | wait | ConsequencePlanner **proposed** deltas earlier | validate deltas, commit state+event in one txn | -> NARRATING |
| **NARRATING** | narrate committed result | (system) | wait | DMNarrator (cannot change numbers) | send output; **retry narration only** on failure | -> TABLE_OPEN or SCENE_TRANSITION |
| **SCENE_TRANSITION** | close/hand off a scene | (system) | read | narrate a transition (never "anything else?") | distill scene to events, advance time if needed | -> SCENE_FRAMING |
| **COMBAT_INITIALIZING** | roll initiative, order | (system) | wait | none | build encounter, initiative order | -> COMBAT_ACTIVE |
| **COMBAT_ACTIVE** | run turns | committing player on their turn | act on turn | narrate turns concisely | validate action economy, turn order | -> COMBAT_RESOLVING_TURN, -> TABLE_OPEN (combat end) |
| **COMBAT_RESOLVING_TURN** | resolve one turn / interrupt | (system) | wait | narrate | resolve attack/damage/HP, handle interrupt | -> COMBAT_ACTIVE |

### Invariants
- **TABLE_OPEN is the default resting state.** Ordinary conversation is never
  blocked and mutates no game state. The machine only advances into the resolution
  path on a `!` committed action (or a pending clarification being answered).
- CLARIFICATION_REQUIRED holds exactly one pending action. Other players' normal
  messages remain answerable, but no state mutates until the pending action
  resolves or is cancelled.
- No microstates that make normal conversation impossible.

## Committed-action pipeline stages (resumable, keyed by discord_message_id)
```
RECEIVED -> INTERPRETED -> ADJUDICATED -> RESOLVED -> COMMITTED -> NARRATED -> SENT
```
`ProcessedMessage.stage` records progress. Reprocessing a message resumes from its
recorded stage and never double-applies effects. **Critical invariant:** if stage
>= COMMITTED and narration fails, the engine retries **narration only** — it never
re-rolls or re-executes the action.

## Error-recovery behavior (summary; see orchestration code)
| Failure | Behavior |
|---|---|
| LLM timeout / invalid JSON | bounded retry, then safe fallback for that job |
| Interpretation confidence too low | request clarification instead of guessing |
| DB txn failure pre-commit | roll back; stage unchanged; safe to retry whole step |
| Dice resolved but commit fails | roll back dice+event together (same txn); retry from ADJUDICATED |
| State committed but narration fails | retry narration only using committed result |
| Discord redelivers same message | idempotency: resume from recorded stage |
| Discord send fails | retry send; stage stays NARRATED until SENT confirmed |
| NPC context retrieval fails | cautious in-character non-answer fallback |
