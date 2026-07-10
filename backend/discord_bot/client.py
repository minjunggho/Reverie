"""discord.py client wiring. Thin: parse event -> (admin | game) bridge -> post.

Setup commands (`!rv ...`) are checked FIRST and go to the AdminBridge, so a leading
`!rv` is never mistaken for a committed `!` character action. Everything else goes to
the game bridge.
"""
from __future__ import annotations

import discord

from app.core.logging import get_logger
from app.discord_bridge import AdminBridge, DiscordBridge, InboundMessage, is_admin_command

log = get_logger(__name__)

DISCORD_LIMIT = 2000


def _chunks(text: str, size: int = 1900):
    """Split a long message on line boundaries to respect Discord's 2000-char limit."""
    if len(text) <= size:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > size:
            if cur:
                out.append(cur)
            # A single very long line is hard-split.
            while len(line) > size:
                out.append(line[:size])
                line = line[size:]
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


class ReverieClient(discord.Client):
    def __init__(self, bridge: DiscordBridge, admin: AdminBridge, **kwargs) -> None:
        intents = kwargs.pop("intents", None) or discord.Intents.default()
        intents.message_content = True  # required to read message text
        super().__init__(intents=intents, **kwargs)
        self.bridge = bridge
        self.admin = admin

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
            if is_admin_command(message.content):
                result = await self.admin.handle(inbound)
            else:
                result = await self.bridge.handle_inbound(inbound)
        except Exception:  # never crash the gateway on one bad message
            log.exception("handling failed for message %s", message.id)
            await message.channel.send("⚠️ เกิดข้อผิดพลาดภายใน ลองใหม่อีกครั้ง")
            return

        for out in result.responses:
            for chunk in _chunks(out.content):
                if out.private_to_discord_id is not None:
                    user = await self.fetch_user(int(out.private_to_discord_id))
                    await user.send(chunk)
                else:
                    await message.channel.send(chunk)
