"""Entry point for the live Discord bot: `python -m discord_bot.run` (from backend/).

Requires DISCORD_BOT_TOKEN and (for real play) a non-fake LLM provider + its key.
Ensures the schema exists for local SQLite; production uses Alembic.
"""
from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger, redact_database_url
from app.core.versions import (
    PROCESS_HOSTNAME,
    PROCESS_INSTANCE_ID,
    PROCESS_PID,
    PROCESS_STARTED_AT,
    git_sha,
)
from app.db.session import get_database
from app.engine import build_default_bridges
from app.rules_content import get_registry
from discord_bot.client import BotInstanceInfo, ReverieClient
from discord_bot.instance_lock import BotInstanceLock, DuplicateBotInstanceError

log = get_logger(__name__)


async def _amain() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    # Validate rules content before any other startup prerequisite can mask it.
    get_registry()
    if not settings.discord_bot_token:
        raise SystemExit("DISCORD_BOT_TOKEN is not set; cannot start the bot.")

    instance_lock = BotInstanceLock(settings.discord_bot_token)
    try:
        instance_lock.acquire()
    except DuplicateBotInstanceError as exc:
        raise SystemExit(str(exc)) from exc

    try:
        db = get_database(settings)
        if settings.is_sqlite:
            await db.create_all()

        game_bridge, admin_bridge = build_default_bridges()
        client = ReverieClient(
            game_bridge,
            admin_bridge,
            instance_info=BotInstanceInfo(
                pid=PROCESS_PID,
                hostname=PROCESS_HOSTNAME,
                instance_id=PROCESS_INSTANCE_ID,
                git_sha=git_sha(),
                database_url=redact_database_url(db.url),
                process_started_at=PROCESS_STARTED_AT,
            ),
        )
        async with client:
            await client.start(settings.discord_bot_token)
    finally:
        instance_lock.release()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":  # pragma: no cover
    main()
