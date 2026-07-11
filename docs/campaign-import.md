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

Free-form prose is rejected instead of being silently promoted to canon. This first slice
imports reviewed locations and their connections. AI extraction, NPCs/factions/secrets,
per-item editing, and automatic travel transitions remain later slices.
