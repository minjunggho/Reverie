# Narration pipeline: the scene packet and how to extend it

This is the map of how a committed game result becomes cinematic, character-specific
Thai prose — and, more importantly, how to add a **new kind of narrative context**
without touching the parts that guarantee correctness.

The governing rule (see [ai-boundaries.md](ai-boundaries.md)): the LLM receives a
**scene packet** of already-committed facts and writes prose. It never rolls, never
computes a number, never writes state, and never sees a restricted fact — because the
packet is assembled from visibility-filtered, engine-owned data *before* generation.

## The flow

```
player "!" action
  → interpret → adjudicate → deterministic dice/spell/attack resolution
  → ConsequencePlanner (proposes) → DeltaApplier (validates + commits)   ← state is now fixed
  → assemble the SCENE PACKET from committed state
  → DMNarrator (writes prose over the fixed facts)
  → narration_guard (screens) → deliver
```

Everything left of "state is now fixed" owns numbers and truth. Everything right of it
is presentation. Narration cannot contradict the packet because the packet is the only
thing it is given.

## The scene packet

Assembled by [`build_narration_context`](../backend/app/memory/context_builders.py).
Each block is produced by a dedicated, testable builder and is **bounded** — the packet
is a relevance-selected briefing, never a dump of the character sheet or campaign log.

| Block | Source builder | What it carries |
|---|---|---|
| `LOCATION` / `AREA` / `OBVIOUS` / `CONDITION` / `ACTIVITY` / `EXITS` / `PRESENT_NPCS` / `LOCAL_CANON` | [`SceneContextBuilder`](../backend/app/memory/scene_context.py) | Canonical, visibility-filtered scene the narrator frames *from* (never invents). Weather/activity are persistent location state, so a hazard evolves across turns instead of resetting. |
| `PREVIOUS_NARRATION` | pipeline (`Scene.spotlight["last_narration"]`) | Last turn's paragraph, so this turn continues the scene instead of restating it. |
| `CAMPAIGN_DIRECTION` / `OPEN_LEADS` | [`ProgressionContextBuilder`](../backend/app/memory/progression_context.py) | Where the campaign is going, every turn. |
| `STALL` | [`StallService`](../backend/app/services/scenes/stall_service.py) | Only present when the party is genuinely circling. |
| `ACTOR` / `PRESENT_PLAYER_CHARACTERS` / `PRESENT_ENTITIES` / `TARGETS` | `SceneEntityDirectory` | Typed cast, so actor/target names never swap and no stranger is invented. |
| `NARRATIVE_PACING` | [`select_pacing`](../backend/app/ai/pacing.py) | Engine-chosen intensity tier — the LLM never picks its own length. |
| `CONSEQUENCE_CLASS` / `NARRATION_HINT` | `ConsequencePlanner` | Direction of the beat (a failure that changes the scene, not "you failed"). |
| `CHARACTER_CONTEXT` | [`CharacterNarrativeContext`](../backend/app/memory/character_context.py) | Relevance-selected appearance, hooks, conditions, faith, recent events. |

### Relevance selection (the important part)

`CharacterNarrativeContext` is the reference example of the discipline every context
builder must follow:

- **Structural non-invention.** Every field reads an existing column or a
  PUBLIC/PARTY event. Nothing is generated. If a fact is not stored, it cannot appear.
- **Surface only when it matters.** A hook appears only when it shares a keyword with
  the moment; `fear`/`flaw` auto-surface on saving throws; **faith** surfaces only for a
  divine action (or an explicit faith keyword) and only from a **PUBLIC** belief profile
  — a SECRET believer's deity never reaches the table-facing packet.
- **Bounded.** Hooks capped, recent events capped, the previous-narration block
  truncated. The packet stays small.

### Pacing tiers (narration modes)

`select_pacing` maps engine-known signals to `QUICK / STANDARD / DRAMATIC / CINEMATIC`
(the spec's Concise/Standard/Cinematic/Climax). An ordinary unlocked door is QUICK; a
save tied to established trauma, a critical, or a session opening is CINEMATIC. The tier
is a fact in the packet; [`thai_dm_style`](../backend/app/ai/prompts/thai_dm_style.py)
tells the narrator to follow it and never pad to length.

## The session opening (`!rv session start`)

`!rv session start` does **not** render a campaign synopsis. It is the one live opening
narrator and follows the same "state first, prose second" law as an ordinary turn.

```
AdminBridge._session_start
  → SessionOpeningService.resolve_opening_location   (continuity-first; never a default tavern)
  → open_new_session: persist Session/Scene/positions/DecisionWindow   ← state is now fixed
  → ScenePacketBuilder.build(narration_mode="session_opening")         (bounded projection)
  → provider.generate_session_opening → OpeningScene (connected Thai prose)
       ↳ on LLMError: _fallback_opening builds a real scene from the SAME packet
  → cinematic_scene_screen(metadata, narration, decision_prompt, planning controls)
  → ONE MessageKind.SCENE_FRAME (data.storytelling_pipeline_version == 2)
```

**There is no legacy card path here.** The old `CampaignPrologue` renderer
(`generate_campaign_prologue`, `PROLOGUE_SYSTEM` — the world→crisis→approach→the-party
"cards" like *เหตุการณ์ที่เปลี่ยนทุกอย่าง* / *เส้นทางสู่พวกเจ้า*) is **not** invoked by the
opener; if a table still sees those sections, it is running a build from before this
pipeline landed. `test_storytelling_pipeline_v2.py` asserts those labels are absent and
that the one delivered message is a connected `SCENE_FRAME`.

### First session vs later sessions

The campaign's **first** session (`number == 1`) opens with a grander, world-establishing
beat — `CAMPAIGN_OPENING_SYSTEM` + the `campaign_opening` template + a packet enriched
with **PUBLIC** world canon (`ScenePacket.world_canon`, top `CampaignCanonRecord` facts by
importance, DM-only truth SQL-filtered). It still resolves to **one connected cinematic
scene** (no synopsis cards): world/era → the place now → the party → the hook → decision,
Realm-Harrowfen style. Later sessions use the tighter `OPENING_SYSTEM`/`session_opening`
and carry no `world_canon`. The offline fallback mirrors this — a first-session fallback
prepends a world paragraph (brief + public canon) so even a model outage yields an epic
intro, not a bare room. Selected in `SessionOpeningService.open_new_session`.

**Delivery is fault-tolerant.** A `SCENE_FRAME` renders as a native Components V2
`LayoutView`; if that send fails (unsupported gateway build, component limits, an API
rejection) the client falls back to flattened text + buttons
(`ReverieClient._send_flattened_screen`) rather than letting the whole opening vanish —
the failure mode behind "session start showed nothing."

### The cinematic-style contract (Realm-reference rubric)

The behavioural target — placing every character *inside* one moving scene rather than
summarising an action — is enforced in three layers, all Thai-first:

- [`OPENING_SYSTEM`](../backend/app/ai/prompts/system_prompts.py): rest the camera on
  each PC in turn, weave **one** grounded packet fact (appearance / gear / vow / injury)
  into a physical action, keep the world already mid-action with a *specific* NPC/
  environment activity, render NPC dialogue as quoted lines, close on one open decision.
- [`THAI_DM_STYLE`](../backend/app/ai/prompts/thai_dm_style.py) → *ขยายการกระทำให้เป็นฉาก*:
  expand a short player intent into a scene, **preserve the player's own monologue
  verbatim** as a spoken line, narrate casting as *channel → verified effect* (never
  decide the result), let allies and enemies visibly act.
- [`thai_narration_templates`](../backend/app/ai/prompts/thai_narration_templates.py):
  per-beat guides, including `session_opening`, `player_monologue`, and `casting`.

The **fallback** (`_fallback_opening`) is held to the same shape deterministically: it
weaves each character's single most-telling stored fact into an active placement line,
keeps the world's `current_activity` verbatim, folds the objective and clock into prose,
and closes on a decision — so an offline table still gets a scene, never a status list or
a generic card. Locked by `test_narration_cinematic_style.py`.

## Voicing the character's line (battle cries, commands, prayers)

The reference's most-loved beats are lines the narrator *supplies* from a terse
intention — `"เพื่อบาฮามุท!"` from "a battle cry full of faith", `"บุก! อย่าให้เหลือสักตน!"`
from "command everyone to charge", a prayer from "Bless Rhaegar". This is **allowed and
bounded**, not free rein:

- **Permitted only when the player's action invokes an utterance** (shout / command /
  chant / prayer / spell verbal). `THAI_DM_STYLE` and `NARRATOR_SYSTEM_EXTRA` let the
  narrator voice a short line **for the ACTOR only**, in that character's voice.
- **A deity/oath may be named only from canon.** A cry can invoke a god *only by the name
  in `CHARACTER_CONTEXT.faith.deity`* — read from the character's **PUBLIC** belief
  profile. No stored deity ⇒ the cry stays secular. The model never invents a god,
  oath, or doctrine.
- **The data must reach the narrator.** `_relevant_faith`
  ([`character_context.py`](../backend/app/memory/character_context.py)) surfaces the
  deity for a divine cast (`is_divine_action`) **or** when the action explicitly invokes
  faith (`_FAITH_INVOCATION_TERMS`, substring-matched for space-less Thai) — so a battle
  cry of faith on a *non-cast* action still carries the god's name. A SECRET belief is
  filtered out first and never surfaces, even when invoked.
- **Still forbidden:** inventing a *new* decision, commitment, opinion, or emotion the
  player did not submit, and speaking for another player's character.

Locked by `test_cast_narration_context.py` (scenario 11 + secret-no-leak) and
`test_narration_cinematic_style.py`.

## Mechanics vs narration are separate objects

The committed roll/spell line is delivered as its own `CHECK_RESOLUTION` message; the
prose is a separate `SCENE_FRAME`. This is why a cast can read as a blessing over a
battered holy symbol while the `+1d4 / slot spent` stays crisp and unmissable. Both the
dice-ritual path and the spellcasting path (`_narrate_cast`) follow this shape.

## How to add a new kind of narrative context

Say you want the narrator to be able to reference, e.g., a character's **carried
trophies** when relevant.

1. **Store the truth somewhere the engine owns.** A column, an event, or a domain
   service — never a value the LLM will later be asked to invent.
2. **Add a bounded, relevance-gated field to a context dataclass** (usually
   `CharacterNarrativeContext` or `SceneContext`). Follow the pattern in
   [`_relevant_faith`](../backend/app/memory/character_context.py): read the stored
   value, gate it on a real signal (keyword overlap / an explicit `is_*` flag), respect
   visibility, cap the size, and render it in `as_block()`.
3. **Thread it through** `build_character_narrative_context` → the pipeline call sites
   (`_resolve_commit_narrate`, `_build_check_setup`, `_narrate_cast`). New parameters
   default to off, so every existing call keeps its behaviour.
4. **If it needs a prompt hint,** add one line to `NARRATOR_SYSTEM_EXTRA` /
   `thai_dm_style` — but the block itself is the context, not a plea. Do **not** solve a
   context gap by enlarging the system prompt.
5. **Prove the three invariants with tests**, mirroring
   [`test_character_narrative_context.py`](../backend/tests/test_character_narrative_context.py)
   and [`test_cast_narration_context.py`](../backend/tests/test_cast_narration_context.py):
   it surfaces when relevant, it stays absent when not, and it never leaks a
   hidden/secret or invents a fact that was not stored.

### What NOT to do

- Do not parse committed state back out of generated prose — store it directly (state
  updates are engine-owned: `ConsequencePlanner` → `DeltaApplier`).
- Do not let a builder read DM-only/secret data into a player-facing block; retrieval is
  visibility-filtered at the query, not by asking the model to keep a secret.
- Do not give the narrator a number to invent or a die to roll. If it is a fact, it
  belongs on the left side of "state is now fixed".

## Where the tests live

- Relevance / non-invention: `test_character_narrative_context.py`
- Pacing tiers: `test_narrative_pacing.py`
- Spell narration + faith + missed attack + secret-belief leak: `test_cast_narration_context.py`
- Repeat guard (detector + previous-beat wiring): `test_repeat_narration_guard.py`
- Companions in one scene + hazard across turns: `test_scene_packet_continuity.py`
- Pre-roll fiction / no premature resolution: `test_check_setup_ritual.py`
- Post-roll payoff / grounded decision prompt: `test_post_roll_payoff.py`
- Session opening path + no legacy cards + shared planning: `test_storytelling_pipeline_v2.py`, `test_opening_context_wiring.py`
- Opening cinematic-style contract + grounded fallback: `test_narration_cinematic_style.py`
- End-to-end cinematic benchmark: `test_cinematic_eval_fixture.py`
- Continuity across a long session / NPC memory: `test_narrative_continuity.py`, `test_session_continuity.py`, `test_npc_memory.py`
