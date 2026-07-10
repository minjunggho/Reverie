"""Entry point for the live Discord bot: `python -m discord_bot.run` (from backend/).

Requires DISCORD_BOT_TOKEN and (for real play) a non-fake LLM provider + its key.
Ensures the schema exists for local SQLite; production uses Alembic.
"""
from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import get_database
from app.engine import build_default_bridges
from discord_bot.client import ReverieClient

log = get_logger(__name__)


async def _amain() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.discord_bot_token:
        raise SystemExit("DISCORD_BOT_TOKEN is not set; cannot start the bot.")

    db = get_database(settings)
    if settings.is_sqlite:
        await db.create_all()

    game_bridge, admin_bridge = build_default_bridges()
    client = ReverieClient(game_bridge, admin_bridge)
    async with client:
        await client.start(settings.discord_bot_token)


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":  # pragma: no cover
    main()
