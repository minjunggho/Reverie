# DM Studio UI (E6)

The owner-only surface of the Reverie Activity — a campaign control room, not
a database admin panel. Server-side role gate on every endpoint; the studio
switch is not even rendered for players.

## Screens

| View | Shows |
|---|---|
| **Command Center** | campaign status + central question, current session/scene, imported session purpose, party positions with HP/conditions, active threats with restrained progress lines, due scheduled events, consistency warnings (e.g. stale scene NPC refs), recent significant events with visibility badges |
| **Current Scene** | three explicitly separated blocks: **สถานที่ (canon)** — the Location truth with exits; **สถานะฉากปัจจุบัน** — purpose/dramatic question/pending action/allowed clues; **ผู้ที่อยู่ในฉาก** — participants + NPCs whose canonical position actually matches, with stale refs surfaced as warnings, never rendered as present |
| **World** | hierarchical explorer (REGION→SETTLEMENT→DISTRICT→LOCATION), search, provenance filter (เขียนเอง/นำเข้า/AI ขยาย), party markers, NPC counts, detail sheet with obvious/focused/hidden layers and canonical travel edges. (A node-graph renderer was intentionally skipped — the polished hierarchy explorer ships instead of a broken map.) |
| **NPCs** | list with location/presence/communication-mode; detail sheet separates **ความจริงของโลก** (objective canon) → **กฎที่ NPC นี้รู้** (protocols, ordered) → **สิ่งที่ NPC นี้รู้/เชื่อ** (epistemic records with status + confidence) → relationships → recent events. Never flattened together. |
| **Factions & Threats** | goal, status, thin progress line (no quest bars), next action, tick schedule, scheduled events with perceivability. Read-only: progress changes only through in-game events. |
| **Secrets & Clues** | secrets with reveal state and per-clue discovery status (● ปาร์ตี้รู้แล้ว / ○ ยังไม่ถูกค้นพบ), plus campaign protocols with their exact ordered rules and known-by list. Never served by any player endpoint. |
| **Events** | canonical inspector: readable summary first, expandable technical detail (actor/targets/real time/mechanical changes), visibility filter segmented control, pagination via `before_seq`. |
| **Campaign Imports** | filename/uploader/hash/status/counts/warnings; **approve / reject / repair** through `CanonImportService` with confirmation dialogs, toasts, and refresh — the Activity's only mutations. |

## Rules

- No raw JSON by default; technical payloads are opt-in expansion.
- Secret and public information are visually distinct (`DM_ONLY` red badges,
  gold/green for revealed/party-known).
- No controls that teleport NPCs, set HP, or write threat progress — those are
  not domain operations, so they do not exist here even as disabled buttons.
