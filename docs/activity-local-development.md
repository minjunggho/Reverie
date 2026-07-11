# Activity — Local Development

## Prerequisites

- Node 20+ (`node --version`)
- Python backend running (`uvicorn app.main:app --reload` from `backend/`)

## Mock mode (no Discord, no backend needed)

```bash
cd activity
npm install
npm run dev
# open http://localhost:5173/activity/?mock=1
```

`?mock=1` (dev builds only) swaps the API client for `MockApi` over the Last
Funeral of God fixtures — full Grimoire + DM Studio with realistic data. Mock
mode is unreachable in production: `detectLaunchMode()` requires a dev build
(or the Playwright-only `&e2e=1` pair on a local preview server).

## Against the real backend

```bash
# terminal 1
cd backend && uvicorn app.main:app --reload           # :8000

# terminal 2
cd activity && npm run dev                            # :5173, proxies /api → :8000
```

To test inside Discord you need a public HTTPS tunnel:

```bash
cloudflared tunnel --url http://localhost:5173
# or: ngrok http 5173
```

Point the Discord Activity URL Mapping at the tunnel URL (see
`docs/activity-deployment.md`), then launch the Activity from a channel of a
server where your app is installed.

## Commands

| Command | What |
|---|---|
| `npm run dev` | Vite dev server |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | Vitest unit tests |
| `npm run build` | typecheck + production build → `dist/` |
| `npm run e2e` | Playwright E2E + screenshot matrix (builds must exist: run `npm run build` first) |

Backend Activity tests: `cd backend && python -m pytest -q tests/test_activity_api.py`
