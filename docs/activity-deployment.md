# Activity — Deployment & Discord Developer Portal Setup

## Build & serve

```bash
cd activity && npm run build          # → activity/dist
cd ../backend && uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`app/main.py` detects `activity/dist` and serves it same-origin at
`https://<host>/activity` with an SPA fallback. Frontend and API share one
origin — no CORS, no separate frontend host. (If a CDN split ever becomes
necessary, document the reason and keep exactly one supported topology.)

## Environment (backend `.env`)

```
DISCORD_CLIENT_ID=<application id — the only value the frontend ever sees>
DISCORD_CLIENT_SECRET=<oauth client secret — server only>
REVERIE_ACTIVITY_SESSION_SECRET=<long random string>
REVERIE_ACTIVITY_SESSION_TTL_MINUTES=120
```

Never put the client secret, bot token, DB credentials, or signing secret in
any `VITE_*` variable — Vite inlines those into public JS.

## Discord Developer Portal checklist (must be done by the app owner)

1. **discord.com/developers/applications → your app.**
2. **OAuth2 → General**: note the Client ID / reset & copy the Client Secret
   into the backend env. Add redirect `https://<host>/activity` (the SDK's
   authorize flow requires at least one redirect registered).
3. **Activities → Enable Activities** (toggle on; requires the app to be in at
   least one server).
4. **Activities → URL Mappings**: Root Mapping `/` → `https://<host>`
   (the Activity loads `https://<host>/activity` through Discord's proxy; all
   `/api/...` calls stay same-origin through the same mapping).
5. **Activities → Settings → Entry Point**: default entry point command is
   created automatically when Activities are enabled; leave it, or customize
   the name players see in the Activity launcher.
6. Install the app to your test server, open a channel, click the Activity
   launcher (rocket icon), pick Reverie.

> These steps are configuration in Discord's portal — the repository cannot
> perform them. Nothing in this file implies they have already been done.

## Local tunnel for portal testing

Discord requires HTTPS for URL mappings. For a dev machine:

```bash
cloudflared tunnel --url http://localhost:8000     # after npm run build
```

Map `/` → the tunnel hostname, then launch from Discord.

## Security invariants in production

- `?mock=1` is inert in production builds (dev-only check + e2e pair).
- Session tokens die at TTL; restart invalidates dev-fallback-signed tokens.
- All authorization is re-checked per request from the database.
