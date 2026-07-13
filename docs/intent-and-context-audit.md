# Player-intent engine + context-retrieval audit

## Ordered ActionPlan (this phase)

Compound `!` actions are interpreted as a typed, ordered `ActionPlan`
(`app/schemas/llm_io.py`: `ActionStep` + `ActionPlan`) and executed step by step
through the SAME single-action routing every simple action uses — no parallel
mechanics. The interpreter (LLM) owns splitting + classifying; the engine sequences.

- **Step kinds:** SPEAK, ATTACK, CAST, INTERACT, SEARCH, MOVE, HIDE, USE_ITEM,
  TRANSFER_ITEM, TRANSFER_CURRENCY, WAIT, OTHER — each with targets/method/
  destination/spell/condition/`depends_on_previous`.
- **Temporal separation:** `IMMEDIATE` (do now) vs `FUTURE` (only stated —
  "เดี๋ยวจะไป") vs `FLAVOR` (description). FUTURE/FLAVOR are **preserved but never
  executed** — this is what stops "ขอบคุณค่ะ แต่เดี๋ยวหนูต้องไปทำธุระ" from teleporting.
- **Ordering + interruption:** `_execute_plan` (`pipeline.py`) runs each executable
  step, committing between steps, so a later step sees the world an earlier one
  left. A physical step that produced no state change (blocked move, unreachable
  target, auto-failure) HALTS the chain; remaining steps are reported as not
  attempted ("earlier consequence prevented the rest").
- **Backward compatible:** empty `steps` → the existing flat-flag routing runs
  unchanged (the common single-action case). Only ≥2 IMMEDIATE steps take the
  ordered path. `preset_interpretation` lets the executor dispatch one step through
  `_process` without re-interpreting or recursing into the plan branch.
- **Natural following** ("ฉันตาม Kael ไป" / "I follow Kael" / "ฉันอยู่ที่นี่")
  reuses the existing consent system (`PositionService.set_follow`/`stop_follow`,
  co-location enforced) via `_handle_follow` — no new follow mechanism.

## Context-retrieval audit — no job sends the whole campaign

Every LLM job assembles a **bounded, purpose-scoped** context in
`app/memory/context_builders.py`. There is no "send the campaign each turn" path.

| Job | Context it receives | What it deliberately excludes |
|---|---|---|
| Classification | scene brief (mode/location/purpose/visible entities), speaker, message | history, other scenes, campaign canon |
| Interpretation | scene brief + present-cast directory (names/types only) + the action | backstories, DM-only facts, other locations |
| Adjudication | scene brief + character capabilities + resolved targets + interpretation | full sheet, campaign, other scenes |
| Consequence | scene brief + action + outcome + resolved targets + **this scene's** allowed clues | clues from other scenes, DM secrets not surfaced |
| Narration | canonical scene context (authored location block) + committed result + typed actor/targets | anything not committed; no invented scenery |
| NPC response | **only that NPC's own** epistemic records + the protocols it's authorized to know + its relationship with THIS listener | objective truth the NPC hasn't learned, other NPCs' memories, campaign canon |
| Scene frame | the canonical location block only | invented NPCs/history/places |
| Recap | visibility-filtered events (PUBLIC/PARTY only) for THIS session | DM_ONLY / PLAYER_ONLY facts (filtered in SQL, not by asking the model) |

Key retrieval boundaries already enforced:

- **NPC knowledge** (`build_npc_response_context` + `NPCKnowledgeService.
  facts_npc_may_use` + `NPCMemoryService.recall`) is scoped to one NPC and one
  listener — objective canon the NPC hasn't learned is structurally absent.
- **Recap** (`build_recap_context`) filters by `Visibility` in the query, so
  restricted content can't leak even if the model asked for it.
- **Scene context** for narration is the authored location block, not the campaign.

## Model selection

Model choice is centralized in the provider (`app/ai/llm/`), not per-call in the
jobs. Reasoning-heavy jobs (interpretation, compound planning, adjudication,
consequence) and prose jobs (narration, NPC dialogue) go through the same provider
abstraction; raising reasoning effort for the planning/adjudication jobs
specifically is a provider-config change, not a context change — the context
scoping above already keeps token cost low regardless of model.

**Recommendation (not implemented this phase):** if per-job model tiers are
introduced, interpretation + compound planning + adjudication justify the stronger
model (they decide routing and mechanics); narration + recap + scene-frame can use
the cheaper tier (they only reformat already-committed facts). No job needs the
whole campaign, so none needs a large-context model for context-size reasons.
