"""Discord Activity authentication (E6).

Flow (docs/activity-auth.md):
  frontend Embedded App SDK  →  authorization code
  → POST /api/activity/v1/auth/exchange (code)
  → backend exchanges the code with Discord using the SERVER-side client secret
  → backend verifies the Discord user via /users/@me with the returned token
  → backend upserts the Reverie User row and mints a short-lived HMAC-signed
    Activity session token
  → every subsequent request carries `Authorization: Bearer <token>`

The frontend NEVER supplies its own user/member/campaign identity — every route
re-resolves CampaignMember (and role) from the VERIFIED discord_user_id inside the
token. A forged campaign_id simply resolves to "not a member" (403/404).

The token is a compact HMAC construction (no new dependency):
  base64url(json payload) + "." + base64url(HMAC-SHA256(payload, secret))
Payload: {"uid": users.id, "did": discord_user_id, "name": display, "exp": epoch}.
It carries identity only — never role, never campaign — those are database facts.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets as _secrets
import time
from dataclasses import dataclass

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

DISCORD_API = "https://discord.com/api/v10"

# Process-lifetime fallback so local dev works without configuring a secret.
# Tokens signed with this die on restart; production sets REVERIE_ACTIVITY_SESSION_SECRET.
_FALLBACK_SECRET = _secrets.token_hex(32)


class ActivityAuthError(Exception):
    """Authentication/authorization failure at the Activity boundary."""


@dataclass(frozen=True)
class ActivityPrincipal:
    user_id: str            # Reverie users.id
    discord_user_id: str
    display_name: str


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(data: str) -> bytes:
    pad = -len(data) % 4
    return base64.urlsafe_b64decode(data + "=" * pad)


def resolve_secret(settings: Settings) -> str:
    return settings.activity_session_secret or _FALLBACK_SECRET


def mint_session_token(
    secret: str, *, user_id: str, discord_user_id: str, display_name: str,
    ttl_minutes: int, now: float | None = None,
) -> str:
    payload = {
        "uid": user_id, "did": discord_user_id, "name": display_name,
        "exp": int((now if now is not None else time.time()) + ttl_minutes * 60),
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return f"{_b64e(body)}.{_b64e(sig)}"


def verify_session_token(secret: str, token: str, *, now: float | None = None) -> ActivityPrincipal:
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body = _b64d(body_b64)
        sig = _b64d(sig_b64)
    except Exception as exc:  # noqa: BLE001 - any malformed token is the same failure
        raise ActivityAuthError("malformed session token") from exc
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise ActivityAuthError("invalid session signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ActivityAuthError("malformed session payload") from exc
    if int(payload.get("exp", 0)) < (now if now is not None else time.time()):
        raise ActivityAuthError("session expired")
    if not payload.get("uid") or not payload.get("did"):
        raise ActivityAuthError("incomplete session payload")
    return ActivityPrincipal(
        user_id=str(payload["uid"]),
        discord_user_id=str(payload["did"]),
        display_name=str(payload.get("name") or ""),
    )


class DiscordOAuthClient:
    """Server-side Discord OAuth: code -> access token -> verified user identity.

    The client secret never leaves this process. The Activity's OAuth access token
    is used ONCE (to verify identity) and then discarded — Reverie's own
    short-lived session is the credential the frontend keeps.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def exchange_and_identify(self, code: str) -> dict:
        """Returns {"id": discord_user_id, "display_name": ..., "access_token": ...}."""
        if not self.settings.discord_client_id or not self.settings.discord_client_secret:
            raise ActivityAuthError(
                "Discord Activity OAuth is not configured "
                "(DISCORD_CLIENT_ID / DISCORD_CLIENT_SECRET)")
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                f"{DISCORD_API}/oauth2/token",
                data={
                    "client_id": self.settings.discord_client_id,
                    "client_secret": self.settings.discord_client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if token_resp.status_code != 200:
                log.warning("discord token exchange failed: %s", token_resp.status_code)
                raise ActivityAuthError("Discord code exchange failed")
            access_token = token_resp.json().get("access_token")
            if not access_token:
                raise ActivityAuthError("Discord returned no access token")

            me = await client.get(
                f"{DISCORD_API}/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if me.status_code != 200:
                raise ActivityAuthError("Discord identity verification failed")
            data = me.json()
        return {
            "id": str(data["id"]),
            "display_name": data.get("global_name") or data.get("username") or "Player",
            # Returned so the SDK can complete `authenticate()`; not stored by Reverie.
            "access_token": access_token,
        }
