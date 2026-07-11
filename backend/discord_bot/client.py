"""discord.py client wiring. Thin: parse event -> (admin | game) bridge -> render.

Setup commands (`!rv ...`) are checked FIRST and go to the AdminBridge, so a leading
`!rv` is never mistaken for a committed `!` character action. Everything else goes to
the game bridge. All rendering (embeds, buttons) lives in `render.py`; button clicks
re-enter the same routing path as if the label had been typed.
"""
from __future__ import annotations

import discord

from app.core.logging import get_logger
from app.discord_bridge import AdminBridge, DiscordBridge, InboundAttachment, InboundMessage, is_admin_command
from app.discord_bridge.dto import BridgeResult, OutboundMessage
from discord_bot.render import ChoiceView, _chunks, build_embed

log = get_logger(__name__)


class ReverieClient(discord.Client):
    def __init__(self, bridge: DiscordBridge, admin: AdminBridge, **kwargs) -> None:
        intents = kwargs.pop("intents", None) or discord.Intents.default()
        intents.message_content = True  # required to read message text
        super().__init__(intents=intents, **kwargs)
        self.bridge = bridge
        self.admin = admin

    async def on_ready(self) -> None:  # pragma: no cover - requires a live gateway
        log.info("Reverie bot connected as %s", self.user)

    # --- routing --------------------------------------------------------------
    async def _route(self, inbound: InboundMessage) -> BridgeResult:
        if is_admin_command(inbound.content):
            return await self.admin.handle(inbound)
        return await self.bridge.handle_inbound(inbound)

    async def on_message(self, message: discord.Message) -> None:  # pragma: no cover
        if message.author.bot:
            return
        attachments = []
        for attachment in message.attachments:
            if attachment.size > 1_000_000:
                await message.channel.send("Campaign import files must be 1 MB or smaller.")
                return
            attachments.append(InboundAttachment(
                filename=attachment.filename, content_type=attachment.content_type,
                data=await attachment.read(),
            ))
        inbound = InboundMessage(
            discord_message_id=str(message.id),
            guild_id=str(message.guild.id) if message.guild else "",
            channel_id=str(message.channel.id),
            author_discord_id=str(message.author.id),
            author_display_name=message.author.display_name,
            content=message.content,
            is_bot=message.author.bot,
            attachments=tuple(attachments),
        )
        try:
            result = await self._route(inbound)
        except Exception:  # last resort — the bridge normally shapes its own errors
            log.exception("handling failed for message %s", message.id)
            return
        await self._deliver(message.channel, result)

    # --- delivery ---------------------------------------------------------------
    async def _deliver(self, channel, result: BridgeResult) -> None:  # pragma: no cover
        for out in result.responses:
            await self._send_one(channel, out)

    async def _send_one(self, channel, out: OutboundMessage) -> None:  # pragma: no cover
        view = self._view_for(out)
        embed = build_embed(out)
        target = channel
        if out.private_to_discord_id is not None:
            target = await self.fetch_user(int(out.private_to_discord_id))
        try:
            if embed is not None:
                await target.send(embed=embed, view=view)
            else:
                chunks = _chunks(out.content)
                for i, chunk in enumerate(chunks):
                    await target.send(chunk, view=view if i == len(chunks) - 1 else None)
        except discord.Forbidden:
            if out.private_to_discord_id is not None:
                await channel.send("🤫 มีข้อความลับส่งถึงเจ้า แต่ DM ถูกปิดอยู่ — เปิดรับ DM แล้วลองใหม่")
            else:
                raise

    def _view_for(self, out: OutboundMessage):  # pragma: no cover
        if not out.choices:
            return None

        async def on_choice(interaction: discord.Interaction, label: str) -> None:
            await interaction.response.defer()
            inbound = InboundMessage(
                discord_message_id=f"btn-{interaction.id}",
                guild_id=str(interaction.guild_id or ""),
                channel_id=str(out.channel_id),
                author_discord_id=str(interaction.user.id),
                author_display_name=interaction.user.display_name,
                content=label,
            )
            try:
                result = await self._route(inbound)
            except Exception:
                log.exception("choice handling failed (%s)", label)
                return
            await self._deliver(interaction.channel, result)

        return ChoiceView(out.choices, on_choice)
