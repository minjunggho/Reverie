# Activity Architecture (E6)

Reverie has three surfaces. **The Table** is Discord text/voice — committed
actions, dialogue, narration, rolls, combat, decisions all stay there. **The
Grimoire** is each player's persistent interface inside the Discord Activity.
**DM Studio** is the owner's campaign control room in the same Activity.

The Activity is a **projection and control surface** over Reverie — never a
second source of game truth, never a second chat client.

## Topology

One origin, one backend:

```
Discord Activity iframe
  └─ https://<host>/activity            ← Vite production build, served by FastAPI
       └─ /api/activity/v1/...          ← same-origin JSON API
```

- Development: `vite dev` on :5173 proxies `/api` → FastAPI on :8000.
- Production: `activity/dist` is served by FastAPI at `/activity` with an SPA
  fallback (`app/main.py`). The Discord URL Mapping points `/` at this host.

## Layers

| Layer | Where | Rule |
|---|---|---|
| Auth | `backend/app/auth/activity.py` | OAuth code exchange server-side; short-lived HMAC session token; identity only |
| Routes | `backend/app/api/activity/router.py` | authenticate → authorize (member/role from DB) → call services; no game logic |
| Projections | `backend/app/services/activity/` | `grimoire.py` (player-safe) / `studio.py` (owner-only); plain dicts, never ORM rows |
| Domain | existing services | derivation engine, ResourceEngine, ConcentrationService, InventoryService, EventService, CanonImportService |
| Frontend | `activity/src/` | renders projections; **recomputes nothing**; no authorization decisions |

The frontend never derives a modifier: `skill.total` and `skill.breakdown`
arrive computed by `app/tabletop/rules/derive.py` — the same engine the bot
uses, so `!rv skill arcana` and the Grimoire always agree.

## Frontend shape

```
activity/src/
├── app/           App bootstrap, phase machine, AppContext
├── api/           types.ts (projection mirrors), client.ts, mockClient.ts
├── auth/          Discord Embedded App SDK adapter + launch-mode detection
├── components/    AppShell (topbar, desktop sidebar, mobile bottom nav)
├── design-system/ core.tsx (surfaces/states/sheets/toasts), stats.tsx (HP/pips/breakdowns)
├── grimoire/      Overview · Skills · Spellbook · Features · Inventory · Story · Party · Chronicle
├── dm-studio/     CommandCenter · Scene · World · NPCs · Threats · Secrets · Events · Imports
├── hooks/         useApi (fetch + refresh-on-focus + 30s visible-tab polling)
├── mocks/         Last Funeral of God fixtures (dev-only)
└── styles/        tokens.css (design tokens) + global.css
```

Navigation is hash-based (`#/grimoire/overview`); no router dependency. State
is React context + local component state; no Redux (nothing here needs it).

## Live data

In order of preference, implemented: reliable initial load → refresh on window
focus → 30-second polling only while the tab is visible → manual retry on
error. No WebSocket layer — the deployment story doesn't justify one yet, and
committed Discord play shows up within one poll interval.

## Read-only by design

The only mutations are campaign-import **approve / reject / protocol repair**
— pre-existing `CanonImportService` operations, owner-gated server-side,
confirmed in the UI, committed in a `unit_of_work`. Spell preparation is
displayed but explicitly read-only (`preparation_editable: false`): no domain
service safely supports it outside the rest flow, so no fake button exists.
