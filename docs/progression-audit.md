# Campaign progression & NPC agency — architectural audit

Status: **audit only**. No code changed. This traces the current flow end-to-end and
identifies why a 90-location imported campaign plays as a 2-location campaign.

The headline: **Reverie already has most of the substrate this needs.** The world clock,
consequence layer, NPC epistemics, world graph, and validation are real, well-built
systems. The failure is not missing machinery — it is that the progression layer is
**write-once and read-never**, and there is no objective/lead vocabulary for the engine
to reason with. Nearly every symptom in the brief traces to one of seven root causes
below, and most fixes are *connective* rather than greenfield.

---

## 1. Traced flow (as built today)

```
campaign MD
  └─ canon_import.parse_campaign_file          bespoke `## Location:` markdown blocks
     └─ CampaignProposal                        canon_import.py:137
        └─ _validate → ImportReview             counts + warnings only
           └─ CanonImportService.commit         → Location, LocationConnection, NPC,
              │                                   Faction, Secret, Threat, Protocol,
              │                                   CampaignCanonRecord
              └─ MainStoryService.initialize_from_proposal   canon_import.py:646
                 └─ Campaign.main_story (JSON)  ← WRITTEN HERE. Then effectively frozen.

session 1
  └─ opening_service                            reads main_story ONCE (opening_service.py:505)
     └─ cinematic prologue                      ← the only time the campaign goal reaches an LLM

every subsequent turn
  └─ CommittedActionPipeline._process           pipeline.py:116
     └─ classify → interpret → adjudicate → resolve → commit → narrate
        └─ build_narration_context              context_builders.py:191
           inputs: location block, cast directory, pacing, action, outcome,
                   result, consequence class, hint, targets, character context
           NOT included: campaign goal, chapter, objective, leads, clues,
                         destinations, main_story — anything about direction.
```

**The through-line ends at session 1.** From turn 2 onward the engine never tells the
narrator what the campaign is about, so the narrator does the only thing it can: react to
the last message. Every "narrator loses campaign direction" symptom is downstream of this
single omission.

---

## 2. Root causes

### RC1 — `main_story` is a write-once blob with no runtime readers

`Campaign.main_story` (models/campaign.py:64) holds the goal, leads, deadlines, hidden
truth, and branches. Full inventory of who touches it:

| Caller | Operation | Reachable in play? |
|---|---|---|
| canon_import.py:646 | `initialize_from_proposal` | yes, once at import |
| opening_service.py:505 | `get` | yes, session 1 prologue only |
| admin_bridge.py:857 | `get` | debug command only |
| tests/test_campaign_understanding.py | all mutators | **tests only** |

`record_branch`, `set_goal_status`, `add_lead`, `resolve_lead`, `advance_state`, and
`set_npc_state` have **zero non-test callers**. `is_main_quest_actionable` is never
consulted. The service is a well-designed API wired to nothing — the pipeline never
advances the story, and no prompt ever reads it.

### RC2 — The objective hierarchy has two of its seven levels

The brief asks for: campaign goal → chapter goal → active objective → immediate task →
leads → clues/opportunities → routes → scene actions.

What exists: `Campaign.central_question` (prose), and `Scene.purpose` (prose, per-scene).
**There is no Chapter model and no Objective model** — grep for `class *Chapter|Objective`
returns nothing. The middle of the hierarchy, where "what should the party do now" would
live, is absent. Nothing can be "the active objective" because the concept has no
representation.

`Quest` (consequences.py:106) is the closest thing: it has `QUEST_STATES`, `progress`, and
a `data` JSON documented as "leads/clues/hidden truth". `ConsequenceService.update_quest`
(consequence_service.py:225) is a complete, event-recording upsert. **The importer never
creates a Quest and the pipeline never updates one.** It is an orphan capability — and the
natural foundation for the objective layer.

### RC3 — Clues are prose strings, so unlock relationships cannot be expressed

- `SecretProposal.clues: list[str]` (canon_import.py:105) — free text.
- `Scene.allowed_clues: list[str]` (scene.py:43) — free text, gated on exact-string match.
- `main_story["leads"]: list[str]` — free text.

A clue is nowhere linked to the fact it reveals, the location it unlocks, or the NPC who
holds it. "The engine does not reliably know what clues unlock which destinations" is
literally true: **no field exists that could hold that edge.** The reveal path
(deltas.py, `reveal_fragment`) validates that the LLM only surfaces authored text — good
discipline — but a revealed fragment changes no state. It is narrated and forgotten.

### RC4 — The world clock only ticks on travel and rest

`WorldClockService.advance_time` is the single engine-owned path that moves in-world time,
and it fires threats, faction actions, rumor spread, and NPC availability. It is real and
it works. Its complete caller list:

- travel_service.py:429 (party travel)
- rest_service.py:77 (rest)
- deltas.py:160 (LLM-proposed `advance_time` delta)

**A party standing in one room talking never advances the clock.** The world-consequence
engine — the exact system built to make the world move on its own — is gated behind the
player action the stuck party is not performing. "The world waits indefinitely while
players repeat low-progress actions" is not a tuning problem; it is mechanically
guaranteed by the call graph.

### RC5 — There is no scene lifecycle

`scene_service.py:5` states: *"Scene exhaustion / transition logic lives in `app/scenes`
behaviours reached from the orchestration layer."*

**`app/scenes` does not exist.** The module was never built; the docstring points at
nothing. Scenes close only via an explicit `close_scene` call. Nothing evaluates whether a
scene's purpose is fulfilled, and there is no low-progress counter anywhere in the
codebase (grep: `low_progress|stall|no_progress|scene_complete` → zero hits). Scenes run
forever by construction.

### RC6 — NPC intentions are computed per-turn and discarded

The NPC epistemic layer is the strongest part of the system. `NPCMemory` (npc_epistemic.py)
carries `open_question` + `resolved` — explicitly designed so an unanswered question
survives a change of subject. `NPCRelationship` has eight bounded dimensions.
`decision_service` computes stance, willingness, and disclosure, and the narrator renders
its decision rather than overriding it. This is good architecture.

The gap: `decision_service.followups` (decision_service.py:76, 225–237) are derived fresh
each turn from a suspicion-threshold ladder, used in one prompt, and thrown away. **There
is no `NPCIntention` table.** So an NPC can remember and can react when spoken to, but
cannot:

- carry a plan across turns,
- act while the party is elsewhere,
- initiate contact,
- or pursue a goal the party has not asked about.

"NPC attitudes change numerically without changing behavior" is precise: the numbers
persist in `NPCRelationship`, the behavior they imply is recomputed and lost. This is why
NPCs answer but never *create movement*.

### RC7 — The importer has no progression vocabulary, and silently drops orphans

`CampaignProposal` (canon_import.py:137) accepts: locations, npcs, factions, secrets,
threats, protocols, world_facts, session_prep, starting_location. It has **no** chapters,
objectives, routes-as-entities, clues-as-entities, items, encounters, events, world_clocks,
or progression_rules. Format is bespoke `## Location: X` blocks parsed by `_subfields` —
**not** the fenced-YAML contract the brief specifies. `ImportReview` (canon_import.py:154)
returns counts + warnings; there is no report of what was inferred, ignored, or missing.

Two concrete consequences for the 90-location case:

**(a) Connections require explicit arrows.** `_parse_exits` (canon_import.py:201) only
reads bullets containing `->` or `→` under an `### Exits` subfield. A campaign written as
prose produces few or no `LocationConnection` rows. Roads, streets, alleys, and gates are
not a category the importer knows — they exist only as sentences.

**(b) Fully orphaned locations are accepted in silence.** `validate_world_graph`
(graph_validation.py:146–158) reports a location with no exits only when it has a parent
(→ `SAFE_AUTO_REPAIR`) or has inbound edges (→ `one_way_trap`):

```python
if out == 0:
    if loc.parent_id and loc.parent_id in by_id:   # repairable
    elif inb > 0:                                   # one-way trap
    # out == 0, inb == 0, no parent → NO ISSUE REPORTED
```

A location with **no exits, no inbound edges, and no parent** falls through both branches
and is reported as nothing. That is exactly what prose-imported locations look like. The
graph becomes dust of unreachable islands, validation passes clean, and the owner is told
the import succeeded.

---

## 3. Symptom → root cause map

| Symptom (from brief) | Cause |
|---|---|
| Many locations, players never travel to them | RC7(a)(b), RC2 |
| Locations are descriptions without purpose | RC2 |
| Roads/streets/alleys/gates missing | RC7(a) |
| No clear immediate objective at start | RC2 |
| Main goal not in runtime context | **RC1** |
| NPCs answer but create no movement | **RC6** |
| NPCs wait for the exact right question | RC6, RC3 |
| Clues buried in prose | RC3, RC7 |
| Engine doesn't know clue → destination | **RC3** |
| Scenes continue past their purpose | RC5 |
| Failed check blocks the only route | RC2, RC3 (no alternate-lead concept) |
| New actions replace prior conflicts | RC6 |
| Attitudes change without behavior change | **RC6** |
| Memories don't create follow-up intentions | **RC6** |
| World waits during low-progress turns | **RC4**, RC5 |
| Narrator loses campaign direction | **RC1** |
| Importer stores prose, not relationships | **RC7**, RC3 |
| Unclear what the engine expects from a MD | RC7 |
| No useful import report | RC7 |

Five causes (RC1, RC3, RC4, RC6, RC7) account for essentially all of it.

---

## 4. Reuse vs. build

Deliberately mapping the target architecture onto what exists, per the brief's
"do not create duplicate systems" constraint.

**Reuse as-is (no duplication warranted):**

| Target concept | Existing home |
|---|---|
| Static locations, geography ladder | `Location` (+ `parent_id`, `discovery_state`) |
| Routes | `LocationConnection` (already has `discovery_state`, `access_state`, `provenance`, `traversal_mode`) |
| Factions, world clocks, scheduled events | `Faction`, `WorldClockService`, `ScheduledEvent` |
| Threats / fronts | `Threat` + `_tick_threats` |
| NPC memory, belief, relationships | `NPCMemory`, `NPCFact`, `NPCRelationship` |
| Campaign secrets | `Secret` |
| Lore / world facts | `CampaignCanonRecord` |
| Objective state + progress + events | `Quest` + `ConsequenceService.update_quest` ← currently orphaned |
| Graph integrity | `validate_world_graph` + `safe_auto_repair` |

**Genuinely missing (must build):**

1. `Chapter` + `Objective` (or: promote `Quest` into the objective layer with a chapter FK).
2. `Clue` as an entity with typed `reveals` edges → fact / location / route / NPC / objective.
3. `NPCIntention` — a persisted, engine-owned plan with a trigger and an expiry.
4. Scene lifecycle: purpose satisfaction + low-progress counter + transition.
5. A progression context block in `build_narration_context` (the RC1 fix).
6. YAML `schema_version: "2.0"` import contract + an author-facing import report.

**Note on layering:** the brief's static CampaignSpec / runtime CampaignState split already
half-exists and is worth making explicit — `Location`/`Secret`/`Threat` are spec-like,
while `discovery_state`, `Quest.state`, and `NPCRelationship` are state-like. Several
tables currently mix both (e.g. `Location.discovery_state` is runtime state living on a
spec row). Recommend keeping the split *logical* rather than physically re-normalizing —
a table rewrite would be a large migration for little gameplay gain.

---

## 5. Recommended sequencing

Ordered by gameplay-impact per unit of risk. Each slice is independently shippable and
observable in play.

**Slice 1 — Direction reaches the narrator (fixes RC1).**
Add a progression block to `build_narration_context` carrying campaign goal, active
objective, and 2–4 current leads. Wire `MainStoryService` mutators into the pipeline's
commit step so leads resolve and goals advance. *Highest impact by a wide margin: it is
the difference between a narrator that knows the campaign and one that does not, and it
touches no schema.*

**Slice 2 — Objectives become real (fixes RC2).**
Promote `Quest` into the objective layer; add `Chapter`. Importer emits objectives.
Pipeline updates them via the existing `update_quest` event path.

**Slice 3 — Clues become edges (fixes RC3).**
`Clue` entity + typed reveal targets. `reveal_fragment` deltas start unlocking
locations/routes/objectives instead of only being narrated.

**Slice 4 — The world moves during conversation (fixes RC4/RC5).**
Low-progress counter on the scene; tick the clock on elapsed conversational time; scene
purpose-satisfaction evaluator. The consequence engine already does the rest.

**Slice 5 — NPCs pursue (fixes RC6).**
`NPCIntention`, written from `NPCMemory.open_question` and `decision_service` output,
drained on a schedule by the world clock. This is where NPCs start creating movement.

**Slice 6 — Import contract (fixes RC7).**
YAML `schema_version: "2.0"`, connector inference for prose campaigns, the orphan-location
validation gap closed, and an author-facing import report.

Slices 1 and 4 together address the majority of the "world feels small and static"
experience and require the least new schema.
