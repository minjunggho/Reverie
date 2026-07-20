"""Application settings, sourced entirely from the environment.

Secrets are NEVER hard-coded. `Settings` reads `REVERIE_*` variables plus a few
conventional vendor variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`DISCORD_BOT_TOKEN`).
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./reverie_dev.sqlite3"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REVERIE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Database ---
    # Async SQLAlchemy URL. Defaults to a local SQLite file for dev; tests override
    # this to an in-memory SQLite. Production sets a postgresql+asyncpg URL.
    database_url: str = Field(default=DEFAULT_DATABASE_URL)
    db_echo: bool = Field(default=False)

    # --- LLM provider ---
    llm_provider: str = Field(default="fake")  # fake | anthropic | openai
    llm_model: str = Field(default="claude-opus-4-8")
    llm_max_retries: int = Field(default=2)
    llm_timeout_seconds: float = Field(default=30.0)

    # Vendor credentials (read WITHOUT the REVERIE_ prefix by convention).
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    # --- Discord ---
    discord_bot_token: str | None = Field(default=None, alias="DISCORD_BOT_TOKEN")
    # Render migrated interactive screens (deity, spell preparation, the cinematic
    # session opening) as native Discord Components V2 LayoutViews. When false, the
    # SAME declarative screens flatten to plain text + a ChoiceView — no legacy embeds
    # either way, and every word still reaches the channel. This is a rollout valve,
    # not a second permanent UI. Default OFF: native V2 requires a discord.py 2.6+
    # gateway build and can silently fail on older/edge hosts (openings then vanish),
    # so the reliable text path is the safe default; flip on with
    # REVERIE_DISCORD_COMPONENTS_V2_ENABLED=true once the host is verified.
    discord_components_v2_enabled: bool = Field(default=False)

    # --- Discord Activity (E6) ---
    # Public application/client id — the ONLY Discord credential the frontend may see.
    discord_client_id: str | None = Field(default=None, alias="DISCORD_CLIENT_ID")
    # Server-side only. Used to exchange the Activity authorization code.
    discord_client_secret: str | None = Field(default=None, alias="DISCORD_CLIENT_SECRET")
    # HMAC key for short-lived Activity session tokens. If unset, a random
    # process-lifetime secret is generated (dev convenience: tokens don't survive
    # a restart). Set explicitly in production.
    activity_session_secret: str | None = Field(default=None)
    activity_session_ttl_minutes: int = Field(default=120)

    # --- Misc ---
    log_level: str = Field(default="INFO")

    # Treat blank .env lines (e.g. `REVERIE_DATABASE_URL=`) as "unset" rather than
    # as an empty string that would otherwise override the sensible defaults.
    @field_validator("database_url", mode="before")
    @classmethod
    def _blank_db_url_is_default(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return DEFAULT_DATABASE_URL
        return v

    @field_validator(
        "anthropic_api_key", "openai_api_key", "discord_bot_token", "database_url",
        mode="before",
    )
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    @field_validator("anthropic_api_key", "openai_api_key", "discord_bot_token",
                     "discord_client_id", "discord_client_secret",
                     "activity_session_secret", mode="before")
    @classmethod
    def _blank_secret_is_none(cls, v: object) -> object:
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached process-wide settings."""
    return Settings()
