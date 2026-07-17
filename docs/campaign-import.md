# Campaign world import

The campaign owner attaches one UTF-8 `.json`, `.md`, or `.txt` file (maximum 1 MB) to:

```text
!rv campaign import
```

Reverie validates and previews it. Nothing becomes canon until the owner runs the approval
command returned in the preview. Players cannot import or approve DM canon.

## JSON format

```json
{"version":1,"locations":[
  {"key":"tavern","name":"Grey Wolf Tavern","obvious":"A north door leads outside.",
   "focused":"Fresh cart tracks cross the mud.","hidden":"A concealed hatch.",
   "connections":["square"]},
  {"key":"square","name":"Village Square","obvious":"A stone well stands outside.",
   "connections":["tavern"]}
]}
```

Keys use English letters, numbers, `_`, or `-`. Connections use keys, not database IDs,
and are directional; list both directions when travel should work both ways.

## Markdown/TXT format

```markdown
## Location: Grey Wolf Tavern
### Key
tavern
### Obvious
A north door leads outside.
### Focused
Fresh cart tracks cross the mud.
### Hidden
A concealed hatch.
### Connections
square

## Location: Village Square
### Key
square
### Obvious
A stone well stands outside.
### Connections
tavern
```

Free-form prose is rejected instead of being silently promoted to canon.

## Progression contract (schema_version 2.0)

A campaign is not just a map of places — it is a **progression graph the engine
operates**. The importer accepts the following in addition to locations. All of it is
optional, but a campaign with no chapters plays as a static map (the import report warns
you), because the engine then has nothing to direct the party toward. See
`docs/progression-audit.md` for why each piece exists.

The hierarchy the engine drives: **campaign goal → chapter goal → objective → task →
leads → clues → routes → scene actions.**

- `## Chapter:` — a phase of the campaign with a `### goal`. Chapters advance when their
  required objectives **resolve** (including *failing* them), so a bad roll never
  deadlocks the campaign. Mark a chapter `### optional` to let the party skip it.
- `## Objective:` — one thing to accomplish, with a player-facing `### task`, a
  `### chapter`, and optional `### optional`. Becomes a `Quest`. An objective a clue
  reveals stays hidden until that clue is found.
- `## Clue:` — a discoverable fact that **changes the world** when learned. Its
  `### reveals` bullets are typed edges:

  ```markdown
  ## Clue: หน้าที่ถูกฉีก
  ### location
  old-harbor
  ### reveals
  - location: sunken-dock          # a hidden place becomes routable
  - route: old-harbor->sunken-dock # a connection opens
  - objective: obj-dive            # a hidden objective becomes known work
  - fact: มีท่าเรืออีกแห่งจมอยู่ใต้น้ำ  # the party now simply knows this
  ```

- `### discovery` on a `## Location:` — `KNOWN` (default, routable from the start),
  `DISCOVERABLE`, `HIDDEN`, or `SECRET`. A campaign whose places are all `KNOWN` gives
  clues nothing to unlock; hide the places a clue is meant to reveal.

### Connectivity is repaired and reported

A location with a `### parent` but no exits is automatically connected to its parent
area, so a prose campaign that states containment (not exits) is still walkable. A
location with **no exits, no inbound edges, and no parent** cannot be connected without
inventing canon — the import report lists it as unreachable so you can add an exit or a
parent. The report tells you what was imported, what connectors were inferred, what was
ignored, and what is missing.

### YAML block (preferred)

The same structured data can be supplied as one authoritative fenced YAML block inside
the Markdown document (or as a standalone `.yaml`/`.yml` file), keyed by
`schema_version: "2.0"`. It maps to the **same** canonical schema as the Markdown above
— it is a friendlier surface, not a second model.

````markdown
```yaml
schema_version: "2.0"
campaign:
  name: เมืองท่าที่เรือไม่ออก
  central_question: ใครกักเรือทั้งเมือง
starting_state:
  location: harbor
chapters:
  - {key: ch1, name: ท่าเรือเงียบ, goal: หาว่าทำไมเรือออกไม่ได้}
objectives:
  - {key: obj-ask, name: ถามนายท่า, task: ถามนายท่าเฒ่า, chapter: ch1}
locations:
  - {key: harbor, name: ท่าเรือเก่า, obvious: ท่าเรือหินเก่า, exits: [{to: road}]}
  - {key: road, name: ถนนริมน้ำ, exits: [{to: harbor}]}
lore:
  - เมืองนี้อยู่ได้ด้วยการค้าทางเรือ
```
````

Sections in the 2.0 contract that the importer does not yet consume (`routes`, `items`,
`encounters`, `events`, `world_clocks`, `progression_rules`) are reported as *ignored*
rather than silently dropped.
