# AI vs Engine Responsibility Matrix

The single most important document for correctness. If a row says **Deterministic**,
the LLM has **no code path** to that outcome, and tests prove it.

| Responsibility | Owner |
|---|---|
| Detect `!` prefix / strip marker | Deterministic |
| Classify a non-`!` message | AI |
| Interpret Thai player goal / method / target | AI |
| Decide whether approaching unseen needs Stealth | AI / hybrid |
| Choose ability + skill from the described method | AI / hybrid |
| Propose a DC *band* | AI (proposes) |
| Clamp DC to allowed bands / apply advantage-disadvantage | Deterministic |
| Character ability / proficiency / final modifier | Deterministic |
| Generate a d20 / any die result | Deterministic |
| Compare total vs DC (outcome) | Deterministic |
| Apply damage / change HP / spend resources | Deterministic |
| Validate action legality (economy, range, resources) | Deterministic |
| Propose narrative consequence class + deltas | AI (proposes) |
| Validate & commit canonical deltas | Deterministic |
| Decide NPC wording / voice | AI |
| Retrieve what an NPC is *allowed* to know | Deterministic authorization layer |
| Decide scene narration style (terse vs cinematic) | AI |
| Change world time / tick threats | Deterministic domain service |
| Narrate a committed world-time / threat change | AI |
| Enforce information visibility in prompts | Deterministic retrieval layer |
| Safety limits, tone, difficulty, house rules | Human/owner override (config) |

## AI jobs (each: PURPOSE / INPUT / CONTEXT / OUTPUT / TOOLS / FORBIDDEN / FALLBACK)

### TableMessageClassifier
- PURPOSE: classify a non-`!` message into a MessageCategory.
- INPUT: raw text + light scene context. CONTEXT: immediate.
- OUTPUT: `ClassificationResult { category, confidence, suggested_response? }`.
- TOOLS: none. FORBIDDEN: **any state mutation**.
- FALLBACK: `UNKNOWN` -> minimal safe response.

### ActionInterpreter
- PURPOSE: turn a `!` action into structured intent.
- INPUT: stripped Thai text. CONTEXT: `build_action_interpretation_context`.
- OUTPUT: `ActionInterpretation { goal, method, target_references, declared_constraints,
  risk_awareness, intent_confidence, missing_information }`.
- TOOLS: none. FORBIDDEN: mechanics selection, state mutation, dice.
- FALLBACK: low confidence -> request clarification.

### AdjudicationJudge (RollNecessityJudge + MechanicSelector + DCResolver)
- PURPOSE: decide resolution type + ability/skill + proposed DC band + clarification flag.
- INPUT: interpretation + scene. CONTEXT: `build_adjudication_context`.
- OUTPUT: `AdjudicationDecision { needs_clarification, clarification_question?,
  resolution_type, ability?, skill?, dc_band?, advantage, disadvantage, rationale }`.
- TOOLS: none. FORBIDDEN: computing modifiers, rolling, committing.
- FALLBACK: `ABILITY_CHECK` at Medium band, or clarification.

### NPCResponseGenerator (Phase 11)
- PURPOSE: NPC dialogue + proposed belief/attitude deltas.
- INPUT: NPC-scoped context ONLY. CONTEXT: `build_npc_response_context`.
- OUTPUT: `NPCResponse { utterance, proposed_belief_deltas[], proposed_attitude_delta? }`.
- FORBIDDEN: facts outside the NPC's knowledge; committing deltas.
- FALLBACK: cautious in-character non-answer.

### DMNarrator
- PURPOSE: Thai narration built from a committed result.
- INPUT: committed `ActionResult`. CONTEXT: `build_narration_context`.
- OUTPUT: `Narration { text, style }`.
- FORBIDDEN: changing any number/outcome; adding consequences; revealing hidden info.
- FALLBACK: terse factual Thai narration of the committed result.

### SafeRecapGenerator
- PURPOSE: player-safe recap.
- INPUT: player-visible events only (retrieval enforces). CONTEXT: `build_recap_context`.
- OUTPUT: `Recap { text }`.
- FORBIDDEN: DM-only content (structurally impossible via retrieval).
- FALLBACK: minimal event-list recap.

### PostSessionAnalyzer (Phase 10)
- PURPOSE: player summary + structured private continuity report.
- INPUT: canonical events. CONTEXT: session events + state.
- OUTPUT: `PostSessionReport { player_summary, continuity_report }`.
- FORBIDDEN: inventing canon; treating prose as DB.
- FALLBACK: template summary from raw events.

## The AI-vs-deterministic split, worked example
`! ผมค่อยๆ เดินไปดูตรงหน้าต่าง พยายามไม่ให้ยามเห็น`
- AI: goal="ดูออกไปนอกหน้าต่าง", method="ค่อยๆ เดิน / ไม่ให้ยามเห็น", constraint="ไม่ให้ยามเห็น".
- AI/hybrid: this needs Stealth (DEX). DC band = Medium(15) because a guard is watching.
- **Deterministic:** DEX modifier (+2), proficiency (+2 if proficient), d20 roll (natural),
  total, compare to 15, outcome. The LLM supplies none of these numbers.
- AI: narrate the committed outcome in concise Thai.
