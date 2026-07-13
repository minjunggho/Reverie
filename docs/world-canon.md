# World Canon & Navigation (E5)

## Confirmed root cause (traced 2026-07-10)

1. `Campaign` holds identity/config/time but **no structured campaign bible/canon**,
   player-safe brief, or session preparation. ✔
2. Campaign Markdown import existed only for **locations** (`canon_import.py`) — no
   world facts, factions, NPCs, secrets/clues, threats, session prep, or provenance
   split. ✔
3. `Location` has obvious/focused/hidden/connections/contents/state — good raw
   fields. ✔
4. `SceneBrief` exposed only `mode / purpose / location_name / raw refs`; it
   **never hydrated the location description, exits, parent geography, or local
   state** into AI context. ✔
5. `build_narration_context` was sparse (SCENE/ACTION/OUTCOME/RESULT/TARGET) — no
   location sensory detail, spatial anchors, exits, conditions, local canon, active
   pressure, or recent events. So the narrator had to **invent the world**. ✔
6. Session-1 generation was fed table profile + hooks + one location string — it
   authored campaign fiction. ✔
7. `LocationService.latest_location()` used the **most-recently-created** location as
   continuity — not the party's actual physical position. ✔
8. There was **no travel pipeline**: `! ผมเดินออกไปข้างนอก` had no
   current-location → exit → canonical-destination → time → world-tick → new-scene
   flow. The narrator, lacking a destination, asked the player to supply one
   ("เจ้าเห็นอะไร?"). ✔ ← the observed failure.

## Product law

**AUTHORITATIVE_WORLD** (default `campaign.config.world_mode`): the player controls
their character (actions, dialogue, feelings, voluntary decisions, things their
character intentionally creates); Reverie owns and frames the world (places, weather,
NPC presence, environment, consequences, information). Reverie may ask what the
*character* does/says/feels; it must never ask a player to author an objective world
fact. A `COLLABORATIVE` mode may relax this later (not built).

## What this slice adds

- **Geography + travel graph.** `Location` gains `location_type`, `parent_id`
  (WORLD→REGION→SETTLEMENT→DISTRICT→LOCATION, any levels optional), `provenance`
  (AUTHORED / IMPORTED / AI_EXPANDED). A new **`LocationConnection`** is the
  authoritative edge (from/to, label, direction, travel_minutes, obvious, one_way,
  access_state) — the free-form `connections` JSON is superseded (kept for
  back-compat, mirrored on import).
- **Canonical position.** `Character.location_id` is where a character physically
  is. Scene presence derives from co-location. Party splits are representable
  (Veskan on Bellmaker Street, Aria in the tavern) without a parallel-sim rewrite.
- **Campaign canon.** `Campaign` gains `brief` (player-safe), `central_question`,
  `session_prep` (JSON). A new **`CampaignCanonRecord`** (category / fact /
  visibility / provenance / importance / scope) is the general lore+clue layer.
  Existing models are reused, not duplicated: **Secret** = DM secrets, **NPC** +
  epistemic records = characters and their knowledge/belief, **Threat** +
  **ScheduledWorldEvent** = factions/fronts and world pressure, **Location** =
  places, **Event** = history of record, **KnowledgeRecord** = provenance of who
  knows what. `CampaignCanonRecord` fills the remaining gap (world-bible facts and
  clues) and links to those via `scope_type/scope_id`.
- **Importer v2.** Full section parser → structured `CampaignProposal` with counts,
  **warnings**, and provenance (EXPLICITLY_AUTHORED / AI_NORMALIZED / AI_PROPOSED).
  Nothing commits without owner confirmation; commit is atomic.
- **TravelService.** Natural movement → resolve exit against the graph → validate
  access → advance the world clock (ticks threats/events) → move the character →
  transition the scene → **narrate the canonical destination** (never invent it).
- **WorldExpansionService.** When a player seeks an unauthored ordinary place, it
  proposes a canon-consistent `LocationDraft` from bounded settlement context,
  commits it (provenance AI_EXPANDED) *before* narration, and it persists. AI may
  expand the world; AI may not rewrite a committed location.
- **SceneContextBuilder.** Bounded, task-specific, authorization-aware canonical
  context (location obvious desc, exits, parent geography, present PCs/NPCs, local
  state, active threats, allowed clues, recent events) for narration/interpretation
  — so the DM already knows the world.
- **Session-1 from prep.** Imported `session_prep` (opening location, present NPCs,
  current activity, allowed clues, protected secrets) constrains the opening; the DM
  keeps presentation/adjudication freedom, not freedom to discard the campaign.
- **Anti-hallucination.** `screen_world_authoring()` deterministically detects and
  rewrites narrator/DM output that asks players to invent objective world facts
  ("เจ้าเห็นอะไรข้างนอก?", "เมืองนี้ชื่ออะไร?"). Fact-provenance policy documented;
  the narrator receives canonical context instead of a "don't hallucinate" line.

## Connective geography — multi-hop navigation + the outside rule (§4–5)

`resolve_exit` answers only "which *adjacent* edge does this reference mean?". That
teleports: a player at the tavern who says "ไปมหาวิหาร" names a place several hops
away with no direct edge. **`RouteService`** (`app/world/route_service.py`) closes
the gap — the engine finds the destination anywhere in the reachable world and routes
to it through the connective geography.

- **Pathfinding.** `find_route` is Dijkstra over `LocationConnection` by
  `travel_minutes`, ties breaking toward fewer hops (a direct authored edge always
  wins). It only crosses **open** edges — a locked/blocked/hidden gate is not a path.
- **Destination resolution + classification.** `resolve_destination` matches a
  reference against every authored location's name (conservative substring, never
  fuzzy), picks the closest reachable match, and returns a `DestinationClass`:
  `EXISTING_ADJACENT` · `EXISTING_ROUTED` · `ORDINARY_EXPANDABLE` · `UNREACHABLE` ·
  `AMBIGUOUS`. A named place with no open route is `UNREACHABLE` (a locked vault, not
  a lie); an unnamed request is `ORDINARY_EXPANDABLE` (the caller may expand).
- **The outside rule.** Because the graph is authoritative, a correct graph already
  routes tavern → street → … → shop through the exterior. `route_obeys_outside_rule`
  makes the invariant checkable: a hop straight from one interior location to another
  is a violation **unless** they are rooms of one building (one is the other's parent,
  or they share an *interior* parent). Two buildings sharing a *district* do not —
  you must step outside between them.
- **Connector inference (sparse worlds).** When a named place is `UNREACHABLE` only
  because a sparse import gave a building no way OUT, `infer_exterior_link`
  deterministically (no LLM) commits the minimum edge from the interior to its parent
  (bidirectional, persisted, idempotent), then re-routes. The world stays explorable
  without ever routing THROUGH an unrelated building.

**TravelService** now consults `RouteService` between the adjacent-exit fast path and
expansion: a multi-hop route traverses the whole path — the world clock advances by
the **summed** travel time (ticking threats/events across the journey), the actor
moves to the final destination, and the arrival frame's footer shows the compressed
route ("· ผ่าน จัตุรัสตลาด"). Detail is compressed; time and world state still reflect
every hop. A real-but-unreachable place is declined with a focused note — never
fabricated. **Bug fixed:** an empty `direction` field no longer counts as "outside"
in `resolve_exit` (§4: an empty direction must never mean outside).

Reachability stays enforced two ways: `campaign_validation._reachable_from` (a BFS
over the proposal graph) blocks committing a world with stranded locations, and
`RouteService` guarantees runtime traversal + inference so the committed world is
always navigable. Tests: `tests/test_connective_geography.py` (11).

## Fact provenance (anti-hallucination)

Every narrative fact must be one of: IMPORTED_CANON · AUTHORED_CANON ·
COMMITTED_EVENT · CURRENT_STATE · AUTHORIZED_KNOWLEDGE · VALIDATED_WORLD_EXPANSION ·
PRESENTATION_DETAIL. **PRESENTATION_DETAIL** is narrow: non-persistent sensory
phrasing consistent with canon (e.g. "ฝนเย็นกระแทกหน้าต่างเป็นจังหวะ" when canonical
weather is heavy rain). The narrator must not invent named NPCs, factions, gods,
history, symbols, items, passages, documents, quests, major locations, political or
religious or magic-system facts unless from canonical context or a validated
expansion.
