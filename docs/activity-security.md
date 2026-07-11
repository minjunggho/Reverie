# Activity Security (E6)

## Threat model & mitigations

| Threat | Mitigation |
|---|---|
| Forged identity | Identity comes only from the HMAC session token minted after a server-side Discord code exchange; signature + expiry checked per request |
| Forged campaign/member/character ids | Every route re-resolves `CampaignMember` from `(campaign_id, verified discord_user_id)`; non-members get 403, unknown campaigns 404 |
| Frontend role escalation (`is_dm=true`, headers) | Role is read from the database row; request-supplied role signals are never parsed |
| DM data reaching players | Player projections live in a module that has no code path into Secret/DM_ONLY queries; JSON-level tests assert secret markers are absent from every grimoire payload |
| Cross-player privacy | PLAYER_ONLY events are witness-filtered in SQL/projection, not in React; party view omits other characters' exact HP |
| Token theft persistence | Token lives in memory only (no localStorage), TTL-bounded, revoked by restart when using the dev fallback secret |
| Secret leakage via config endpoint | `GET /config` returns only the public client id |
| Mock mode in production | `detectLaunchMode()` requires a dev build for `?mock=1`; production builds show the outside-Discord fallback instead |
| Guild spoofing | `guild_id` mismatch with the campaign's stored guild unbinds the channel context |

## Rules for future Activity endpoints

1. Add the route under `/api/activity/v1` with `principal_from_header`.
2. Resolve member + role through `_member_or_403` / `_require_owner`.
3. Build responses in `services/activity/` — never serialize ORM rows.
4. Player-safe and DM-only data must come from **separate builders**; never
   return a combined payload for the client to filter.
5. Mutations must call an existing validated domain service inside
   `unit_of_work`, and surface domain conflicts as 409s.
6. Add a JSON-level test proving restricted markers are absent.
