# PROGRESS — Reverie

**Status:** MVP + experience overhaul + rules evolution (SRD 5.2.1) + P0 multiplayer
identity fix + E5 campaign canon & world navigation + P0 playtest correctness fix +
**E6 Grimoire Activity + DM Studio (SHIPPED)**.

## E6 — Grimoire Activity + DM Studio (2026-07-11)

### Shipped

**Architecture implemented.** A real Discord Activity: React 18 + TypeScript +
Vite frontend (`activity/`) over a new authenticated Activity API
(`/api/activity/v1`). Production build served same-origin by FastAPI at
`/activity` (SPA fallback in `app/main.py`); dev uses the Vite proxy. The
Activity is a projection/control surface only — every derived number
(modifiers, breakdowns, DCs, passives, resources, concentration) is computed
by the existing derivation engine server-side; React recomputes nothing and
authorizes nothing.

**Auth flow.** Embedded App SDK → authorize (identify+guilds) → code →
`POST /auth/exchange` → server-side token exchange with the Discord client
secret → `/users/@me` identity verification → Reverie User upsert →
short-lived HMAC session token (identity only — role/campaign are re-resolved
from the DB per request). Token in memory only; expiry → re-auth screen.
Forged campaign/member/role/guild values are inert (docs/activity-auth.md).

**Endpoints.** `GET /config`, `POST /auth/exchange`, `GET /context`, player
`GET /campaigns/{id}/grimoire/{overview,skills,spellbook,features,inventory,
story,party,chronicle}`, owner-gated `GET /campaigns/{id}/studio/
{command-center,scene,world,npcs,npcs/{nid},threats,secrets,events,imports}`,
and the only mutations: `POST .../studio/imports/{iid}/{approve,reject,repair}`
via the existing CanonImportService inside unit_of_work.

**Views.** Grimoire: Overview (HP bar w/ temp-HP, conditions, death saves,
concentration banner, stat medallions, resource pips → provenance sheet),
Abilities/Saves/all-18-Skills (sort/filter, tap → real Breakdown + passive),
Spellbook (DC/attack/slots, prepared/concentration/ritual filters, detail
sheets; explicitly read-only preparation — no fake controls), Features (grouped
by provenance with พร้อมใช้/ใช้หมดแล้ว/กลไกยังไม่รองรับ states), Inventory
(search/filter), Story (hooks + own private discoveries), Party (observable
state only; other players' exact HP absent from the payload), Chronicle
(session-grouped journal, visibility filtered in SQL). DM Studio: Command
Center, Current Scene (canon location vs scene state vs present entities kept
distinct; stale NPC refs surfaced as warnings, never as present), World
(hierarchical explorer + provenance filters + canonical edges; deliberate
no-graph-renderer decision), NPCs (objective canon / protocols / epistemic
knowledge / relationships separated), Threats (restrained progress, read-only),
Secrets & Clues (per-clue discovery state + ordered protocols), Events
(inspector w/ visibility filters + expandable technical detail), Imports
(approve/reject/repair with confirmations + toasts).

**Mutations.** Import approve/reject/protocol-repair only — owner-authorized
server-side, confirmed in UI, domain-service-committed, results toasted and
projections refreshed. No set-HP/teleport/set-progress endpoints exist.

**Authorization tests (backend, 13).** Token roundtrip/expiry/bad-signature;
401s for missing/expired sessions; server-side OAuth exchange; context
resolution incl. forged guild + non-member; engine-derived skill totals ==
`skill_bonus` for all 18 skills; JSON-payload absence of DM_ONLY + other
players' PLAYER_ONLY data across all eight grimoire endpoints; own private
discovery visible only to its owner; forged campaign ids (403/404); player
403 on all eight studio endpoints, owner 200; role-from-DB (frontend role
flags ignored); NPC knowledge/canon separation; stale scene refs excluded;
full import lifecycle incl. double-approve → 409.

**Frontend tests (10, Vitest+RTL).** Overview renders real state incl.
concentration + conditions; resource tracker accessible + provenance sheet;
skill breakdown sheet shows INT+Proficiency=total; proficient-only filter;
spellbook concentration banner + prepared filter; DM switch hidden from
players / shown to owners; permission-denied state; mobile bottom nav;
import approve confirmation flow → success toast.

**E2E/screenshots (28 Playwright tests).** 8 screens × {375×812, 768×1024,
1440×900} with a hard no-horizontal-overflow assertion on every capture, plus
NPC-detail sheet, mobile bottom-nav navigation, outside-Discord fallback state,
and a full player→DM navigation journey across all 16 views.

**Visual QA findings (screenshots inspected).** Mobile overview: identity, HP
bar (with hatched temp-HP), concentration banner, medallions and bottom nav
all legible at 375px with no overflow. Desktop Command Center reads as a
control room (warnings, party positions, thin threat progress — no quest
bars). NPC detail cleanly separates canon/protocols/beliefs. Fixed during QA:
full-page screenshot black band (html background), Playwright selector
ambiguity, Vite 6/Vitest type conflict (pinned Vite 5). Remaining nits
accepted: Thai glyph rendering depends on system fonts (no webfont by design —
Discord Activity CSP), tablet uses the mobile nav below 900px.

**Commands.**
`cd backend && python -m pytest -q` · `cd activity && npm run typecheck &&
npm test && npm run build && npm run e2e` · dev: `npm run dev` →
`http://localhost:5173/activity/?mock=1`.

**Known limitations.** Spell preparation is read-only (no safe domain service
outside the rest flow — labeled, not faked). Live updates are focus-refresh +
30s visible-tab polling, not push. The Discord Developer Portal steps (URL
mapping, Enable Activities, entry point) are documented in
docs/activity-deployment.md but must be performed by the app owner — the
repository cannot claim they are configured. Campaign selection outside a
bound channel shows the user's campaigns but deep-launching into one is not
yet wired (open the Activity from the campaign's channel).

### Audit (verified against the repo head before any E6 edit)

- **Current frontend state: none.** No `activity/`, no Node tooling, no static
  assets. The only UI Reverie has is Discord embeds rendered from
  `OutboundMessage`s.
- **Current API state:** FastAPI app factory (`app/main.py`) mounts exactly two
  routers — `/health` (+`/health/db`) and read-only `/admin` observability
  endpoints. No auth of any kind exists on HTTP; the Discord bot is the only
  authenticated surface. No CORS, no static file serving, no session concept.
- **Authorization boundaries available to build on:** `User.discord_user_id`
  (unique), `CampaignMember.role` (`OWNER`/`PLAYER`),
  `CampaignService.resolve_member(campaign_id, discord_user_id)`,
  `Campaign.game_channel_id` (one campaign per channel — the binding used by the
  bot), and `Visibility` enum enforced at retrieval in
  `EventService.list_visible_events` and the E5 scene-context/canon queries.
- **Data projections needed** (all raw material already exists, engine-authoritative):
  - Character sheet math: `tabletop/rules/derive.py` (`skill_bonus`/`save_bonus`
    return `Breakdown(total, parts)` — exactly the "modifier explanation" the
    Activity must show), `passive_perception`, `initiative_bonus`,
    `spellcasting_block`.
  - Resources: `ResourceEngine.get/…` over `ResourceState` + registry
    `ResourceDef` (recharge kind, Thai name).
  - Concentration: `ConcentrationService.current` over `ActiveEffect`.
  - Spells: `CharacterSpell` (+ registry `SpellDef` concise Thai summaries).
  - Grants/provenance: `CharacterGrant` (source_type ∈ CLASS/SPECIES/BACKGROUND/…).
  - Inventory: `InventoryService.list_inventory` → (entry, ItemDefinition).
  - Chronicle: `EventService.list_visible_events` (PUBLIC/PARTY; PLAYER_ONLY needs
    a witness filter added at the query level, not in the client).
  - DM Studio: `Scene`/`Session`/`Location`(+`LocationConnection`)/`NPC`(+
    `NPCFact`/`NPCRelationship` via `NPCKnowledgeService`)/`Threat`/
    `ScheduledWorldEvent`/`Secret`/`CampaignCanonRecord`(clues + protocols)/
    `CanonImport` + `CanonImportService` (approve/reject/repair_protocols are the
    only Activity mutations — all already validated domain operations).
- **Proposed files:** backend `app/api/activity/` (router `/api/activity/v1`),
  `app/auth/activity.py` (Discord OAuth code exchange + HMAC-signed short-lived
  session token; no new dependency — httpx already present), `app/services/
  activity/` (player + DM projection builders returning plain JSON-safe dicts,
  never ORM rows); frontend `activity/` (Vite + React + TS + @discord/
  embedded-app-sdk + CSS-module design system + Vitest/RTL + Playwright).
  Production build served by FastAPI at `/activity` (same origin as the API).
- **Read-only vs mutating:** everything in the Activity is read-only EXCEPT
  campaign-import approve / reject / protocol-repair (existing
  `CanonImportService` operations, owner-gated server-side). Spell preparation
  has no existing domain service ("เปลี่ยนคาถาที่เตรียมไว้ได้หลังพักยาว" is a rest-flow
  concern), so the Spellbook is explicitly read-only and labeled as such — no
  fake controls, no new mutation invented for E6.
- **Reused domain services (no game logic in routes or React):** all of the
  above; routes authenticate → authorize (member/role resolved server-side from
  the verified Discord identity, never from a frontend flag) → call projections/
  services → return JSON.

## P0 playtest correctness fix (2026-07-11)

Scope: fix six real multiplayer-playtest failures only. Does **not** touch character
options, Scene Planner, combat, dice, rests math, or the campaign import location/NPC
pipeline beyond adding one new optional section (Protocol).

### Root-cause verification (confirmed against the repo head before any edit)

1. **Imported ordered protocols had no structured home.** `canon_import.py` only ever
   wrote `Campaign.brief`/`central_question`/`CampaignCanonRecord` (unordered facts).
   A "five numbered rules" block had nowhere canonical to live, so an NPC asked to
   repeat them had nothing grounded to retrieve — confirmed.
2. **`build_npc_response_context` (context_builders.py) fed an NPC only its own
   `NPCFact` rows** (`NPCKnowledgeService.facts_npc_may_use`) — no protocol context
   existed to feed even if it had been structured — confirmed.
3. **Committed (`!`) social actions never reached `NPCSocialService`.**
   `CommittedActionPipeline._resolve_commit_narrate` (pipeline.py) always ran
   adjudication → consequence → the generic `DMNarrator`, regardless of whether the
   action was "ask Mother Veyra a question." The generic narrator (a Thai-prose LLM
   with only ACTOR/TARGETS/OUTCOME text) was therefore the thing inventing NPC
   dialogue and rules — confirmed. `NPCSocialService` (epistemic-scoped, engine-
   validated) already existed and was correct, but only `MessageRouter` (non-`!`
   chat) called it.
4. **`MessageRouter._visible_npc` picked the FIRST npc ref in
   `scene.visible_entity_ids`**, ignoring any name in the player's message —
   confirmed, literally list-order selection, for the *non-committed* chat path.
   (The committed pipeline already used `SceneEntityDirectory.resolve_mentions` for
   targeting per the P0 multiplayer fix — but committed social actions never reached
   an NPC-authorized responder at all, per #3.)
5. **`SceneEntityDirectory.build` trusted `scene.visible_entity_ids` /
   `immediate_threat_ids` unconditionally** — it never checked
   `NPC.current_location_id` against `scene.location_id`. Confirmed as the direct
   cause of "NPCs teleport to every location": nothing ever purged a stale NPC ref.
6. **`TravelService.travel` mutated `scene_row.location_id` in place** and left
   `visible_entity_ids`/`immediate_threat_ids`/`relevant_object_ids`/`allowed_clues`/
   `purpose` untouched — confirmed root cause of #5's symptom: every destination
   scene was really the *origin* scene wearing a new location id, so the origin's
   NPCs stayed "present" forever.
7. **Any `movement_intent=True` with no matching graph exit fell straight into
   `WorldExpansionService.find_or_expand`** (`TravelService.travel`, no gate) —
   confirmed cause of "follow that sound" minting a permanent shop.
   `ActionInterpretation` had only a boolean `movement_intent`, with no distinction
   between canonical travel, local movement, following a source, or searching for an
   unauthored place — confirmed.
8. **`RestService` (`tabletop/rest/rest_service.py`) was fully implemented and
   correct but was never called from `CommittedActionPipeline`** — confirmed;
   `ActionInterpretation` had no rest fields at all, so natural-language rest fell
   through to ordinary ability-check adjudication.

### Fixes (files changed)

- **Protocol representation** — reused `CampaignCanonRecord` (`category="protocol"`,
  `data={"key","rules":[ordered],"known_by":[names]}`) rather than a new table.
  `canon_import.py`: new `ProtocolProposal`, `## Protocol: Title` Markdown block
  (`### Key`/`### Visibility`/`### Known By`/`### Rules`, deterministic parsing —
  no LLM), review counts + validation (unique key, ≥1 rule, order preserved, known-by
  NPCs exist, legal visibility), committed atomically in `approve()`. Added
  `CanonImportService.repair_protocols` — an idempotent, protocol-only backfill for
  an *already-approved* campaign: it parses a **newly uploaded** revision's stored
  `source_text` for `## Protocol:` blocks only and adds any missing-by-key records,
  never touching locations/NPCs/secrets (which would otherwise conflict on
  re-approval). Owner workflow: re-upload the revised file (`!rv campaign import`,
  new draft because the content hash differs), then `!rv campaign import repair
  <new-draft-id>` instead of `approve`.
- **Grounded factual NPC answers** — `NPCKnowledgeService.protocols_known_by` reads
  ordered protocol records where the NPC's name is in `known_by`.
  `build_npc_response_context` now includes a `PROTOCOLS:` block (ordered, verbatim)
  and the NPC's `COMMUNICATION_MODE`. `NPC_RESPONSE_SYSTEM` instructs: if the
  listener asks about a listed protocol, the rules must be reproduced verbatim, in
  order, with no additions/omissions/substitutions.
- **All committed NPC-directed social actions route through `NPCSocialService`** —
  `ActionInterpreter`/`ActionInterpretation` gained `social_intent: bool` (ask/greet/
  thank/threaten/bargain/tell/request-decision). `CommittedActionPipeline` now
  branches before adjudication: if `social_intent` and there is ≥1 resolved NPC
  target, it calls `NPCSocialService.respond()` once per resolved NPC (from
  `SceneEntityDirectory`, never first-in-list) and returns their grounded responses
  directly — no roll, no generic `DMNarrator` rewriting NPC meaning. Physical
  framing (if any) stays server-authored, not model-authored.
- **NPC communication mode** — `NPC.communication_mode` (default `"SPOKEN"`;
  `SLATE`/`SIGN`/`NONVERBAL`/`OTHER`). `NPCResponse` gained optional
  `spoken_text`/`written_text`/`nonverbal_action` (kept `utterance` for backward
  compatibility). `NPCSocialService` — not the prompt alone — deterministically
  composes the final display text: a non-`SPOKEN` NPC's line is rendered as a
  written/nonverbal action, never as attributed spoken dialogue, regardless of what
  the model put in `utterance`.
- **Named NPC target resolution for ordinary (non-`!`) dialogue** —
  `MessageRouter` now builds a `SceneEntityDirectory` and resolves
  `ClassificationResult.target_references` (new field, classifier prompt updated
  in step with the interpreter's own target-extraction contract) through
  `resolve_mentions`, exactly like the committed pipeline. First-NPC selection is
  removed. Exactly one present NPC with no explicit name is still inferred
  (unchanged single-NPC scenes keep working); multiple named targets each get their
  own grounded, communication-mode-correct reply; a genuinely ambiguous mention gets
  one focused clarification question instead of a guess.
- **NPC location hard invariant** — `SceneEntityDirectory.build` now only treats an
  `npc:` ref from `visible_entity_ids`/`immediate_threat_ids` as present when
  `NPC.campaign_id` matches and `NPC.current_location_id == scene.location_id`;
  stale refs are silently skipped (never surfaced to context, dialogue, or
  narration).
- **Real scene transition on travel** — `TravelService.travel` now closes the
  origin `Scene` (`SceneService.close_scene`) and creates a **new** `Scene` at the
  destination: only the traveling player-character refs carry over as
  `participants`; `visible_entity_ids` is rebuilt from
  `NPC.current_location_id == destination` (never inherited); `relevant_object_ids`/
  `immediate_threat_ids`/`allowed_clues`/`purpose`/`dramatic_question` all reset.
  Character `location_id` moves (`PositionService`, already correct) are preserved.
  Returning later creates yet another fresh scene at the same canonical `Location` —
  no stale opening replay.
- **Precise movement-intent typing** — `ActionInterpretation` gained
  `movement_kind: CANONICAL_TRAVEL | LOCAL_MOVEMENT | FOLLOW_SOURCE |
  SEARCH_FOR_PLACE | RETURN_OR_EXIT | REST | NONE` alongside the existing boolean
  `movement_intent` (kept for backward compatibility with scripts that don't set
  `movement_kind` — those default to the pre-fix behavior, which is exactly the old
  "resolve exit, else expand" path, so the pre-existing E5 travel/expansion tests
  are untouched). `CommittedActionPipeline` now branches on `movement_kind`:
  `FOLLOW_SOURCE`/`LOCAL_MOVEMENT` never enter `TravelService` at all (adjudicated
  normally, in place); `CANONICAL_TRAVEL`/`RETURN_OR_EXIT` call
  `TravelService.travel(..., allow_expansion=False)` — a failed match returns a
  focused "which way?" clarification, never a new `Location`; only
  `SEARCH_FOR_PLACE` (and the unset/legacy default) allows
  `WorldExpansionService`.
- **Natural-language rest routing** — `ActionInterpretation` gained `rest_intent`,
  `rest_kind` (`short`/`long`/`ambiguous`), `rest_scope` (`actor`/`party_request`).
  `CommittedActionPipeline` routes a rest intent to `RestService` before
  adjudication: `ambiguous` asks one clarification
  ("พักสั้นหนึ่งชั่วโมง หรือพักยาวคืนนี้?"); otherwise it calls
  `RestService.short_rest`/`long_rest` and renders the real `RestOutcome`
  (completed vs. interrupted) — the narrator never touches rest numbers.
  **Documented limitation**: `party_request` is accepted by the schema but the
  engine still only rests the acting player's own character in this slice (no
  multi-player consent flow yet) — a solo player can never be forced to sleep by
  another player's action.

### Tests added

`tests/test_playtest_fixes.py` — the 16 scenarios from the playtest report against
an extended Last Funeral of God fixture (new `## Protocol:` section; `Black Chapel`
location; `Mother Veyra`/`Father Caldus`/`Sister Nara` NPCs, Sister Nara
`communication_mode=SLATE`): exact five-rule recall in order with no invented rules;
Sister Nara never produces spoken dialogue; named-NPC resolution among three present
NPCs; multi-NPC thanks with per-NPC correctness; leaving Black Chapel produces a
clean destination scene while Veyra/Caldus/Nara stay behind; returning later is a
new scene, not a replay; searching for a smith may create one persistent shop;
following a vague sound creates no new `Location`; leaving a generated shop returns
via the canonical reverse edge (no new valley/forge); short/long/ambiguous/
interrupted rest; PC rest agency (actor-only); no secret leak through protocol/NPC
grounding; stale `scene.visible_entity_ids` refs are excluded from the directory.

### Known remaining limitations

- Party rest requests do not yet have a consent flow; only actor-only rest is wired.
- `FOLLOW_SOURCE`/`LOCAL_MOVEMENT` are adjudicated as ordinary ability checks in this
  slice — there is no dedicated "tracking/investigation" resolution table yet; that
  is reasonable groundwork for the (separate, out-of-scope) Scene Planner.
- The protocol repair path only backfills protocols; a campaign whose *locations or
  NPCs* changed after approval still has no general re-sync path (unchanged from
  before this fix — out of scope here).

### Final test run

`cd backend && python -m pytest -q` → **148 passed**, 4 failed. All 16 new
`test_playtest_fixes.py` tests pass, all 9 `test_world_canon.py` tests pass against
the extended fixture. The 4 failures (`test_acceptance_journey.py::
test_two_player_full_journey`, `test_character_options_expansion.py::
test_finalize_character_persists_planned_subclass`, `test_experience_overhaul.py::
test_guided_creation_conversation_produces_hooks`, `test_experience_overhaul.py::
test_sheet_v2_and_skill_explanation`) are **pre-existing and unrelated** — confirmed
by stashing every file this fix touched and re-running: they fail identically on
that baseline. Their root cause is entirely inside the separate, already-in-progress
character-option-expansion work (`build_flow.py`/`finalize.py`/`character.py`/
`rules_content/*`, none of which this fix touches) — a new "choose planned subclass"
Stage B step now appears where those tests still expect the old Species step next.
Out of scope for this fix per explicit instruction not to touch character options.

## E5 — campaign canon & world navigation (2026-07-10)

Fixes the observed failure where the DM asked players to author the world
("เจ้าเห็นอะไรข้างนอก?"). Root cause + design: docs/world-canon.md.

- **Geography & travel graph.** `Location` gains `location_type`/`parent_id`/
  `provenance`/`weather`/`current_activity`; new `LocationConnection` is the
  authoritative directed edge (label/direction/travel_minutes/obvious/one_way/
  access_state). `WorldGraphService.resolve_exit` maps natural movement
  ("ออกไปข้างนอก", "ขึ้นชั้นสอง", a destination name) to a canonical edge — never
  by list order, never invented.
- **Canonical position.** `Character.location_id`; `PositionService` (where/move/
  co-located). Session start places every attendee at the opening location. Party
  splits are representable.
- **Campaign canon.** `Campaign.brief`/`central_question`/`session_prep`; new
  `CampaignCanonRecord` (category/fact/visibility/provenance/importance/scope) for
  world-bible facts + clues. Reuses (no duplicate truth): Secret=DM secrets,
  NPC+epistemic=characters, Threat+ScheduledWorldEvent=factions/fronts+pressure,
  Location=places, Event=history, KnowledgeRecord=provenance.
- **Importer v2** (`canon_import.py`): full-section Markdown/JSON parser (identity,
  brief, central question, world facts, locations+geo+exits, factions, NPCs,
  secrets+clues, threats, Session 1 prep) → structured proposal with counts +
  **warnings** (NPC without goal/location, secret with <2 clues, unknown refs).
  Owner reviews (`!rv campaign import`), confirms, commits atomically. Nothing is
  canon before confirmation; the old locations-only path still works.
- **TravelService** (the fix): natural movement → resolve exit → advance world clock
  (ticks threats/events) → move the party → transition the scene → **frame the
  destination FROM CANON**. The narrator never invents the destination.
- **WorldExpansionService**: an unauthored ordinary place is proposed from bounded
  settlement context, committed (provenance AI_EXPANDED) with a connection + optional
  proprietor NPC BEFORE narration, and **persists** — the same request returns the
  same location, never a regenerated duplicate.
- **SceneContextBuilder**: bounded, authorized canonical context (location obvious
  desc, exits, parent geography, present cast, local canon, active threats, allowed
  clues, recent events) — feeds narration so the DM already knows the world. DM
  secrets never enter a player-facing block.
- **Session 1 from prep**: imported `session_prep` sets the opening location, present
  NPCs, current activity, and allowed clues; the opening generator is constrained by
  it (presentation freedom, not freedom to discard the campaign).
- **Anti-hallucination** (`narration_guard.py`): `screen_narration` /
  `screen_decision_prompt` deterministically strip/rewrite world-authoring questions
  ("เจ้าเห็นอะไร?", "เมืองนี้ชื่ออะไร?") from committed narration — a structural
  guard, not a prompt plea. Fact-provenance policy documented.
- **World pressure continues**: travel advances the clock, which ticks Threats/
  ScheduledWorldEvents — the world doesn't freeze when players ignore the plot.

**Tests** (`tests/test_world_canon.py`, 9 + the Last Funeral of God fixture):
import review identifies all sections + warnings; atomic canon commit (geo/graph/
NPCs/secrets/clues/factions/threats/prep); Session 1 opens at the imported location
with prep; `! เดินออกไปข้างนอก` transitions to the canonical connected location and
never asks the player to author scenery; travel advances time + ticks threats;
AI-expanded location persists across a return; narration screen blocks bad DM
questions; secrets don't leak into player scene context.

**Limitations (documented):** party travels together by default (explicit "stay"
splits deferred though position tracking supports them); world expansion covers
ordinary places only (no plot generation); the deterministic Markdown parser is
EXPLICITLY_AUTHORED — an AI gap-filling analyzer (AI_PROPOSED) is future; a fresh
Alembic autorevision is needed for Postgres (tests use `create_all`; delete the
local dev SQLite once — new columns/tables were added).

---

## P0 multiplayer identity / party-context fix (2026-07-10)

**Confirmed root cause (traced against the code, all 9 points verified):**

1. `DiscordBridge.handle_inbound` correctly resolves the SENDER → CampaignMember →
   active Character (the actor). ✔ actor identity was never the bug.
2. `CommittedActionPipeline._process` loads scene + acting character + campaign and
   passes **only the acting Character** into the interpreter/adjudicator. ✔ confirmed.
3. `build_action_interpretation_context` emits `SCENE / CHARACTER / ACTION` where
   CHARACTER is only the actor — no directory of the other present PCs. ✔ confirmed.
4. `SceneBrief` carries mode/purpose/location/visible_entities but **never hydrates
   `scene.participants`** — the party refs exist on the scene but reach no prompt. ✔.
5. `visible_entities` are passed as **raw refs** (`character:abc`, `npc:xyz`) with no
   canonical name or type, so the model can't tie "Aria" to `character:<aria_id>`. ✔.
6. Session OPENING is party-aware (it hydrates all attendees' names/hooks); that
   context is thrown away for normal active play. ✔ confirmed the asymmetry.
7. `ActionInterpretation.target_references` is extracted but the pipeline **never
   resolves or uses it**. ✔ confirmed.
8. `_primary_npc_target(scene)` returns the **first `npc:` ref by list order** from
   `immediate_threat_ids + visible_entity_ids` — ignores `target_references`, never
   resolves a player-character target, and mis-targets (`! ผมขู่พ่อค้า` → Guard). ✔.
9. `build_narration_context` receives only a raw `target_ref` string — no typed
   actor/target/present-party identity. ✔ confirmed.

**Net:** the party's canonical identities live on `scene.participants`, but nothing
downstream of session-open hydrates them, resolves target mentions against them, or
distinguishes a present player-character from an NPC. So another player's character
mentioned by name reads as an unknown person.

**Architecture of the fix (see below in this doc + docs/multiplayer-identity.md):**
a SceneEntityDirectory hydrates the *present* entities (PC vs NPC, canonical name,
controller) — presence = `scene.participants`/`visible_entity_ids`, **not** party
membership; an EntityResolver maps `target_references` → canonical refs with
conservative precedence; the pipeline resolves ONCE at the engine boundary and
propagates typed actor+targets into adjudication/consequence/narration; PC-agency
is structurally enforced (the engine refuses to execute a command over another PC's
voluntary choice — not merely a narrator instruction); `_primary_npc_target` is
replaced by resolved-target selection.

**Architecture changes (files):** `app/entities/` (SceneEntityDirectory +
EntityContext + resolver); `Character.aliases`, `Scene.spotlight`,
`ActionInterpretation.commands_other_pc`; party-aware context builders +
interpreter/adjudicator/consequence/narrator jobs; pipeline resolves target
mentions once, gates ambiguity/presence/PC-agency, threads typed targets through
the dice ritual, records resolved targets on the committed event, and bumps
spotlight; `_primary_npc_target` (first-by-order) removed — NPC consequence target
is the resolved NPC or the scene's *immediate threat*; router preserves dialogue
speaker identity. Prompts updated (interpreter target_references + commands_other_pc;
narrator actor/target non-swap + PC-agency). See docs/multiplayer-identity.md.

**Migration:** additive columns only (`characters.aliases`, `scenes.spotlight`);
`create_all` covers tests; delete the local dev SQLite once; Postgres gets an
Alembic autorevision. Existing campaigns remain usable.

**Tests added (`tests/test_multiplayer_identity.py`, 14):** actor mapping per
sender · party name → existing PC (no NPC invention) · Thai alias · command-over-PC
refused · declaring another PC's action refused · physical action on unconscious PC
allowed · NPC target by name not list order · same-name ambiguity → one clarify ·
absent party member not reachable · Discord name ≠ character alias · actor/target
distinct in directory · party view == scene directory · dialogue speaker preserved ·
no PLAYER_ONLY leak into another player's action context.

**Remaining multiplayer limitations (documented, not regressions):**
- No multi-scene parallel simulation — a split party shares one active scene;
  absent PCs are known-but-not-present (correct), but two simultaneous sub-scenes
  aren't modeled yet.
- Contextual/pronoun target resolution ("the guard", "her") beyond exact
  canonical/alias is deferred — unmatched mentions fall through to normal handling.
- Aliases are explicit (`Character.aliases`); no automatic Thai transliteration and
  no `!rv alias` command yet.
- Spotlight is awareness only (last_actor + counts); no active "spread the
  spotlight" director behavior.

---

**107 → 124 passing automated tests** (`cd backend && python -m pytest -q`),
including a dedicated multiplayer identity suite.

## Rules evolution (2026-07-09) — what changed
Baseline researched and documented: **SRD 5.2.1, CC-BY-4.0** (docs/rules-sources.md,
docs/rules-coverage-audit.md, docs/next-architecture.md).

- **Rule Definition System**: versioned JSON content (`app/rules_content/srd_5_2_1/`)
  — 6 classes (L1 chassis), 4 species with traits, 4 backgrounds (2024-style: ASI
  trio + Origin feat + 2 skills + tool + equipment), 30 spells with Thai summaries,
  resource definitions. Loaded by a validated read-only registry
  (ruleset_id/definition_id/definition_version).
- **Character v2**: species/background split from backstory; saves, Expertise,
  languages, tool proficiencies, temp HP, speed, Hit Dice, death saves, Exhaustion,
  dying/stable/dead. Grant provenance (`CharacterGrant` — "where did I get this?"),
  spells with kind/prepared state, `ResourceState`, `ActiveEffect`.
- **Derivation engine** (`tabletop/rules/derive.py`): every derived value computed
  with an explainable breakdown — `!rv skill arcana` answers "ทำไมถึง +5" with the
  actual composition. HP/AC/passives/spell DC never AI-assigned.
- **Resource engine + rests**: generic max formulas + recharge semantics (long rest,
  short-rest partial, Arcane-Recovery cycle). Short/Long Rest are domain operations
  on the world clock; a perceivable world event inside the window **interrupts** the
  rest (no benefits, 2024 restart rule).
- **Damage pipeline**: typed components resolved independently (Resistance/
  Vulnerability/Immunity before totaling), temp HP absorbs first (keep-higher),
  dying/death saves/instant death per SRD, healing revives; damage triggers
  **Concentration saves** (DC max(10, dmg/2)); one concentration effect at most.
- **Two-stage creation**: Stage A conversation → reflection card (facts heard, no
  mechanics) → Stage B guided build where the **player chooses everything** —
  class/species/background (⭐ recommended, never auto-applied), Standard Array
  arrangement, background ASI, class skills (legal counts, duplicate handling),
  species trait choices, Rogue Expertise, cantrips/spellbook/prepared — finalized
  in one transaction with full provenance + starting gear.
- **Dice ritual**: campaign `dice_mode` (default PLAYER_CLICK) — visible checks
  pause at a CHECK_PROMPT [🎲 ทอย d20]; the SERVER rolls on tap; ROLL and NARRATION
  arrive as separate messages. AUTO preserves immediate resolution. Hidden checks
  stay silent.
- **Clarification restraint**: engine gate — an adjudicator's clarification is
  honored only when the interpreter also found material missing info ("! แอบฟังต่อไป"
  with an established conversation never asks "ฟังเรื่องอะไร?"). Prompts updated
  with the counter-example; A/B/C choice prompts banned in narration.
- **Failure with teeth**: `Scene.allowed_clues` + `reveal_fragment` delta — the
  model may *time* a partial reveal ("...ไม่ใช่ของมนุษย์") but only from authored
  clue text; inventions are rejected.
- **Views v2**: full sheet (saves ●, Expertise ★, passives, initiative, speed, Hit
  Dice, death saves, spell DC/slots), `!rv spells` (cantrips/spellbook/prepared ✦/
  concentration banner), `!rv skill <name>` breakdown.
- DB additions (additive; delete stale dev sqlite): character v2 columns,
  character_grants, character_spells, resource_states, active_effects,
  scenes.allowed_clues.

**Deferred (designed in docs/next-architecture.md §8):** level-up + subclasses +
executable feats (E2), spell execution engine (E3), combat economy v2 (E4),
campaign Markdown import + DM canon split + blind-owner mode (E5), Grimoire
Activity + DM Studio (E6), Homebrew Lab (E7).

## Experience overhaul (2026-07-09) — what changed
- **Presentation contract**: every player-facing message is kinded
  (`app/presentation`, 21 kinds incl. REVERIE_WELCOME…TECHNICAL_ERROR); the Discord
  adapter renders embeds/colors/buttons (`discord_bot/render.py`); button clicks
  round-trip as typed text (uniform + testable).
- **Guided character creation**: `!rv character` opens a Thai conversation
  (CharacterDraft state machine + CreationGuidance job) → hooks (origin/desire/
  fear/flaw/connection/appearance) stored on Character → CHARACTER_REVEAL with
  class preset + starting gear. Quick path preserved.
- **Session Zero**: `!rv setup` — 4 friendly questions → campaign profile
  (tone/balance/assistance/boundaries).
- **Openings**: Session 1 is AI-generated from a bounded context (profile +
  character hooks + location) and must tie in an established hook; sessions ≥2 get
  recap + place/time continuity. No hardcoded tavern.
- **Readable resolution**: one CHECK_RESOLUTION message = short narration lines +
  visible engine-owned dice line + optional decision prompt. Mechanics never in prose.
- **Error staging**: bridge consults ProcessedMessage.stage — pre-commit failures
  say "nothing happened, retry"; post-commit narration failures restate the
  committed result and never re-execute. No more bare "internal error".
- **Views**: `!rv sheet / inventory / journal / party` (journal derives from
  player-visible events — leak-proof by construction). Items: ItemDefinition/
  InventoryEntry + class starting gear + ITEM_GAINED events.
- **NPC dialogue**: CHARACTER_DIALOGUE at a visible NPC routes to the epistemic
  social service (NPC_DIALOGUE kind, named speaker).
- **Private secrets**: `reveal_secret` consequence delta can only point at a
  PRE-AUTHORED Secret row; delivery is an engine-enforced DM (PRIVATE_SECRET).
- **Closing**: deliberate beat → SESSION_END chronicle (decisions/discoveries/
  items/objectives) → one-tap feedback stored on Session.feedback.
- **Narration policy**: progressive disclosure + banned stock phrases
  (docs/thai-dm-style.md); manual eval fixtures in `backend/evals/`.
- **DB (additive)**: characters.hooks/appearance, character_drafts,
  item_definitions, inventory_entries, sessions.feedback; campaign.config gains
  profile/setup_state keys.

## Key architectural decisions
- **Tests run on SQLite (`aiosqlite`)**; production targets PostgreSQL (`asyncpg`).
  ORM kept dialect-portable (string-UUID PKs, portable JSON). Alembic targets PG.
- **In-process async lock per session** for the serialized action queue (single bot
  process assumed for MVP). Redis deferred until multi-process deployment.
- **LLM never touches state or randomness.** All AI jobs return Pydantic proposals;
  the engine validates + commits. `FakeLLMProvider` drives all tests deterministically.
- Primary keys are `uuid4().hex` strings generated in Python (portable, testable).
- Optimistic concurrency is **manual** (`guarded_version_update` + `version` columns),
  which is explicit and testable.

## Phase status — all complete

| Phase | Goal | Status | Tests |
|---|---|---|---|
| 0 | Docs + scaffolding | ✅ | — |
| 1 | App core, config, DB, health, harness (FakeLLM + deterministic dice) | ✅ | test_phase1_core |
| 2 | Campaign/member/character/session/scene | ✅ | test_phase2_domain |
| 3 | Event model + atomic state+event transactions | ✅ | test_phase3_events |
| 4 | Discord bridge + idempotency + identity + serialized queue | ✅ | test_phase4_bridge |
| 5 | Normal vs `!` routing + TableMessageClassifier | ✅ | test_phase5_routing |
| 6 | ActionInterpreter + clarification model | ✅ | test_phase6_clarification |
| 7 | Adjudication + authoritative dice + committed pipeline | ✅ | test_phase7_dice, test_phase7_pipeline |
| 8 | Thai DM narration + style + consequence model | ✅ | test_phase8_narration |
| 9 | Session opening + player-safe recap | ✅ | test_phase9_opening_recap |
| 10 | Session closing + post-session continuity pipeline | ✅ | test_phase10_post_session |
| 11 | NPC knowledge/belief + basic social | ✅ | test_phase11_npc |
| 12 | World time + threats/scheduler | ✅ | test_phase12_world |
| 13 | Basic combat (initiative/turns/attack/damage/interrupt) | ✅ | test_phase13_combat |
| — | §34 first vertical slice (end-to-end) | ✅ | test_slice_vertical |
| — | §31/§32 concurrency + recovery | ✅ | test_concurrency_recovery |

## §34 first vertical slice — checklist (all covered by test_slice_vertical.py)
- [x] init app / campaign / two members / one character each / location / guard NPC
- [x] start Session 1 / opening scene framed in Thai
- [x] normal message executes NO committed action, mutates NO state, emits NO events
- [x] `!` action: resolve user→character, interpret, decide uncertainty, select Stealth
- [x] server d20 + modifier + DC compare + atomic commit (state+event)
- [x] Thai narration from committed result; scene at rest afterward
- [x] end session; player-safe recap; NO DM-only info in recap

## Definition of Done (§38) — met
- [x] §34 vertical slice passes end-to-end with automated tests
- [x] normal vs `!` routing proven
- [x] LLM provably cannot roll dice, compute modifiers, or mutate canonical state
- [x] information-safety tests pass (recaps + NPC prompts cannot leak)
- [x] concurrency + idempotent recovery tests pass
- [x] opening → active play → closing → post-session with player-safe + private
      continuity artifacts derived from canonical events
- [x] basic social and basic combat function
- [x] docs + PROGRESS reflect the built system

## Supported rules subset (documented in app/tabletop/rules/core.py)
Ability checks, saving throws, single-weapon attacks + damage, flat proficiency bonus
by level, a fixed skill→ability map, a small class/ancestry allowlist. Consequence
deltas limited to a validated allowlist (`advance_time`, `raise_suspicion`, `note`) —
damage/HP only ever come from the deterministic dice/combat path. NOT supported:
spells, subclasses, feats, multiclassing, the full condition set.

## Runnable surfaces
- API: `uvicorn app.main:app --app-dir backend` (health + read-only admin/debug).
- Bot: `python -m discord_bot.run` (from backend/; needs DISCORD_BOT_TOKEN + a real
  LLM provider). Thin adapter over `app.discord_bridge`.
- Composition root: `app.engine.build_bridge` / `build_default_bridge`.

## Deferred (with reason)
- **Redis distributed lock** — not needed for single-process MVP; in-process lock used.
  Swap in for multi-process deployment.
- **Concrete Alembic migration scripts** — env/wiring is in place (async, targets PG);
  run `alembic revision --autogenerate -m "initial schema"` to generate. Tests use
  `create_all` on SQLite and don't depend on migrations.
- **AI-inferred commitment detection** — intentionally not built; only `!` commits.
  The `ActionCommitment` abstraction + reserved `CommitmentSource` values leave room.
- **`failure_progress_level` config flag** — stored and available; the engine never
  auto-escalates a miss (satisfying "never auto-convert every miss"), but explicit
  band-downgrade enforcement from the flag is left as a tuning hook.
- **Full pipeline↔combat wiring** — the combat engine is complete and tested; routing
  a Thai committed action to an in-progress encounter (target resolution from text) is
  the one remaining integration seam. Social responses likewise have a service
  (`NPCSocialService`) ready to be called from the router.
- **NPC memory / suspicion as separate tables** — consolidated into `NPCFact.status`
  (KnowledgeStatus) + `NPCRelationship`; separate `NPCMemory`/`NPCSuspicion` tables
  are a future normalization, not a capability gap.

## How to run
```
cd backend
python -m pytest -q          # 74 tests
uvicorn app.main:app         # from backend/ (or: uvicorn app.main:app --app-dir backend)
```
# P0 multiplayer identity audit (2026-07-10)

Verified root cause: `DiscordBridge` correctly resolves the sending campaign member and that
member's active `Character`, and session opening correctly turns attending members' active
characters into `Scene.participants`. The normal committed-action path then loses the group:
`CommittedActionPipeline._process()` hydrates only the acting character; `SceneBrief` ignores
participants and exposes visible entities as raw refs; interpretation target mentions are never
resolved; `_primary_npc_target()` instead selects the first threat/visible NPC; and adjudication,
consequence, and narration receive no explicit typed actor/target/party identities. Thus a second
PC is neither linguistically discoverable nor protected as player-controlled after scene opening.
Attendance, party membership, and scene presence are stored separately, but the read/context
layer did not preserve that distinction. The fix is a bounded, scene-authoritative entity
directory plus deterministic name/alias resolution, threaded as canonical actor and targets
through adjudication, commitment, and narration, with explicit other-PC agency rules.
