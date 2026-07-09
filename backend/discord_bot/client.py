"""discord.py client wiring. Thin: parse event -> bridge -> post responses."""
from __future__ import annotations

import discord

from app.core.logging import get_logger
from app.discord_bridge import DiscordBridge, InboundMessage

log = get_logger(__name__)


class ReverieClient(discord.Client):
    def __init__(self, bridge: DiscordBridge, **kwargs) -> None:
        intents = kwargs.pop("intents", None) or discord.Intents.default()
        intents.message_content = True  # required to read message text
        super().__init__(intents=intents, **kwargs)
        self.bridge = bridge

    async def on_ready(self) -> None:  # pragma: no cover - requires a live gateway
        log.info("Reverie bot connected as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:  # pragma: no cover
        if message.author.bot:
            return
        inbound = InboundMessage(
            discord_message_id=str(message.id),
            guild_id=str(message.guild.id) if message.guild else "",
            channel_id=str(message.channel.id),
            author_discord_id=str(message.author.id),
            author_display_name=message.author.display_name,
            content=message.content,
            is_bot=message.author.bot,
        )
        try:
            result = await self.bridge.handle_inbound(inbound)
        except Exception:  # never crash the gateway on one bad message
            log.exception("bridge failed for message %s", message.id)
            return
        for out in result.responses:
            if out.private_to_discord_id is not None:
                user = await self.fetch_user(int(out.private_to_discord_id))
                await user.send(out.content)
            else:
                await message.channel.send(out.content)
