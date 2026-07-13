# Campaign understanding

The owner's story is read, preserved, validated, and kept reactive. This builds on
the existing structured-import pipeline (`canon_import.py`: Markdown/JSON parser,
`CampaignProposal`, review + approve/reject, provenance) and adds the three things
the mandate most needed: a **provenance-priority authority**, **deterministic
validation**, and **main-story continuity**.

## Provenance priority (`app/services/campaigns/provenance.py`)

One authority on "who wins" when two facts disagree. The ladder, highest first:

```
IMPORTED_EXPLICIT  →  OWNER_EDITED  →  IMPORTED_SEMANTIC  →  COMMITTED_EVENT
                   →  AI_PROPOSED_CANON  →  AI_INFERRED_CONNECTOR  →  AI_RUNTIME_EXPANDED
```

- `outranks(a, b)` / `priority(p)` — order by authority (legacy strings like
  `IMPORTED`, `IMPORTED_CANON`, `AUTHORED`, `AI_PROPOSED` are aliased onto the ladder).
- `may_overwrite(existing, incoming)` enforces the rules: **AI content never
  overwrites explicit owner canon**; **two owner-explicit facts about one thing are
  a contradiction — returned as "may not overwrite" so the caller surfaces it, never
  auto-resolved**; AI-over-AI updates by rank.

## Deterministic validation (`app/services/campaigns/campaign_validation.py`)

`validate_campaign(proposal) -> ValidationResult` of typed `ValidationIssue`
(kind / severity / message / refs). Engine judgement, never the LLM. Checks:

- duplicate identities (location / npc / faction / secret keys)
- broken references (connections / exits / parents / npc location)
- missing or invalid starting location
- **reachability**: every location must be reachable from the start (undirected BFS
  over connections/exits/parents) — an unreachable important place is an `error`
- NPC without a motive (warning); NPC at an unknown location (error)
- secret without any clue path (error — undiscoverable); thin single-clue (warning)
- threat/main-quest with no next action and no schedule (warning — it will never
  advance)

Errors block commit; warnings surface for review. Complements the parser's existing
hard structural raises.

## Main-story continuity (`app/services/campaigns/main_story.py`)

`Campaign.main_story` (JSON, migration `20260716_mainstory`) tracks the central
storyline so it is never lost and keeps reacting — without railroading:

```
dramatic_question · state · hidden_truth · leads[] · deadlines[] ·
goals[{key,text,status: open|completed|failed|transformed}] ·
branches[{turn,summary}] · npc_states{}
```

`MainStoryService`:
- `initialize_from_proposal` — seeds at import approve (dramatic question from the
  central question, hidden truth from the top secret, leads from secrets' clues +
  threats' next actions, deadlines from scheduled threats, the main goal).
- `record_branch` (a player choice diverged the story — recorded, story kept),
  `set_goal_status` (completed/failed/transformed), `advance_state`, `add_lead` /
  `resolve_lead`, `set_npc_state`.
- `is_main_quest_actionable` — the through-line is live while the main goal is open
  and a lead remains (never a dead end).

Persisted, so it survives restart and many turns; scoped per campaign, so two
campaigns never share a main story.

## Tests (`tests/test_campaign_understanding.py`, 9)

Two unrelated Markdown fixtures. Verifies: validation flags missing-start /
unreachable / broken-ref / secret-without-clue / duplicate-identity; explicit owner
canon outranks all AI and is never overwritten (and owner-vs-owner is surfaced, not
resolved); imported locations carry explicit provenance; important canon (brief /
central question / NPC / secret truth) is unchanged after import; two campaigns
never leak; main story seeded with leads + hidden truth + deadlines; main story
reacts to player branches and survives restart; main quest stays actionable while
open, closes when completed.

## Deferred (honest scope)

- **LLM semantic extraction of freeform prose** — the parser handles structured
  Reverie Markdown + JSON deterministically, and AI-proposed worlds come through
  `campaign_creator.py`. Extracting a rich `CampaignProposal` from arbitrary
  freeform prose / plain paragraphs with per-fact source-excerpt + confidence +
  explicit-vs-inferred tags is a large LLM job not built here; the typed shape it
  would target (provenance ladder + validation + main-story) is now in place for it
  to land against.
- **Granular owner-review UI** — approve-all / approve-selected / edit / reject /
  reject-with-regenerate-one-section / lock-immutable / re-run-validation. Today's
  review is counts + typed validation issues + approve/reject (per E7). The
  validation result is already structured for a richer review surface to render.
- **Main-story auto-reaction wiring** — `MainStoryService` records and exposes the
  state; automatically advancing it from committed play events (a death resolves a
  lead, a deadline firing transforms a goal) is a follow-up that hooks the existing
  event stream into these methods.
