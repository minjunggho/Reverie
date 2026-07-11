# Activity Authentication & Authorization (E6)

## Flow

1. Frontend detects it is inside Discord (`frame_id` query param) and loads the
   Embedded App SDK.
2. `sdk.ready()` → `sdk.commands.authorize({scope: ["identify", "guilds"]})`
   returns an **authorization code**.
3. Frontend POSTs the code to `POST /api/activity/v1/auth/exchange`.
4. Backend exchanges the code at `discord.com/api/v10/oauth2/token` using the
   **server-side client secret**, verifies the user via `/users/@me`, upserts
   the Reverie `User` row, and mints a short-lived Activity session token.
5. Frontend passes the returned Discord access token to
   `sdk.commands.authenticate(...)` to complete the SDK handshake. Reverie does
   not store that token.
6. Every later request carries `Authorization: Bearer <reverie-session-token>`.

## The session token

`base64url(JSON payload) + "." + base64url(HMAC-SHA256(payload, secret))`

- Payload: `{uid, did, name, exp}` — **identity only**. Never role, member id,
  or campaign id: those are database facts re-resolved per request.
- Secret: `REVERIE_ACTIVITY_SESSION_SECRET` (falls back to a random
  process-lifetime secret in dev — tokens die on restart).
- TTL: `REVERIE_ACTIVITY_SESSION_TTL_MINUTES` (default 120). Expired/forged
  tokens → 401; the frontend shows the re-authenticate screen.
- Held in frontend memory only — never localStorage.

## Authorization rules (all server-side)

- Every campaign-scoped route resolves `CampaignMember` from
  `(campaign_id, token.discord_user_id)`. Not a member → 403. Unknown
  campaign → 404.
- Every `/studio/*` route additionally requires `member.role == OWNER`.
- Frontend-supplied role flags/headers/query params are ignored entirely
  (pinned by `test_dm_role_comes_from_database_not_frontend_flags`).
- `guild_id` mismatches unbind channel context rather than trusting it.
- Player projections are built by `services/activity/grimoire.py`, which never
  queries Secret / DM_ONLY records — absence is structural, not a filter in
  React (pinned by JSON-level tests).

## What is never exposed to the frontend

Discord client secret, bot token, database credentials, the signing secret,
other players' PLAYER_ONLY records, DM_ONLY anything on player routes. The
only public credential is `DISCORD_CLIENT_ID` via `GET /api/activity/v1/config`.
