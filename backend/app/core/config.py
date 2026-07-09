"""Application settings, sourced entirely from the environment.

Secrets are NEVER hard-coded. `Settings` reads `REVERIE_*` variables plus a few
conventional vendor variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`DISCORD_BOT_TOKEN`).
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    database_url: str = Field(default="sqlite+aiosqlite:///./reverie_dev.sqlite3")
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

    # --- Misc ---
    log_level: str = Field(default="INFO")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached process-wide settings."""
    return Settings()
