"""discord.py client wiring. Thin: parse event -> (admin | game) bridge -> render.

Setup commands (`!rv ...`) are checked FIRST and go to the AdminBridge, so a leading
`!rv` is never mistaken for a committed `!` character action. Everything else goes to
the game bridge. All rendering (embeds, buttons) lives in `render.py`; button clicks
re-enter the same routing path as if the label had been typed.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import discord

from app.core.config import get_settings
from app.core.logging import get_logger
from app.discord_bridge import AdminBridge, DiscordBridge, InboundAttachment, InboundMessage, is_admin_command
from app.discord_bridge.dto import BridgeResult, OutboundMessage
from discord_bot.components_v2 import build_layout_view
from discord_bot.render import ChoiceView, _chunks, build_embeds, flatten_screen

log = get_logger(__name__)


@dataclass(frozen=True)
class BotInstanceInfo:
    pid: int
    hostname: str
    instance_id: str
    git_sha: str
    database_url: str
    process_started_at: str


class ReverieClient(discord.Client):
    def __init__(
        self,
        bridge: DiscordBridge,
        admin: AdminBridge,
        *,
        instance_info: BotInstanceInfo | None = None,
        **kwargs,
    ) -> None:
        intents = kwargs.pop("intents", None) or discord.Intents.default()
        intents.message_content = True  # required to read message text
        super().__init__(intents=intents, **kwargs)
        self.bridge = bridge
        self.admin = admin
        self.instance_info = instance_info
        self._startup_identity_logged = False

    async def on_ready(self) -> None:  # pragma: no cover - requires a live gateway
        if self.instance_info is None:
            log.info("Reverie bot connected as %s", self.user)
            return
        bot_user_id = str(getattr(self.user, "id", "unknown"))
        if self._startup_identity_logged:
            log.info(
                "Discord gateway reconnected instance=%s bot_user=%s",
                self.instance_info.instance_id,
                bot_user_id,
            )
            return
        identity = {
            "event": "bot_instance_started",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **asdict(self.instance_info),
            "bot_user_id": bot_user_id,
        }
        log.info("Bot instance started %s", json.dumps(identity, sort_keys=True))
        self._startup_identity_logged = True

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
        target = channel
        if out.private_to_discord_id is not None:
            target = await self.fetch_user(int(out.private_to_discord_id))
        try:
            # A declarative screen renders as native Components V2 (or, when the flag
            # is off, flattens to text + a ChoiceView) — never a legacy embed. A V2 send
            # that fails (unsupported gateway build, component limits, API rejection)
            # MUST NOT make the whole message vanish: fall back to flattened text+buttons
            # so a cinematic opening always reaches the table.
            if out.screen is not None:
                on_choice = self._make_on_choice(out)
                if get_settings().discord_components_v2_enabled:
                    try:
                        await target.send(view=build_layout_view(out.screen, on_choice))
                        return
                    except discord.Forbidden:
                        raise
                    except Exception:
                        log.exception(
                            "Components V2 screen send failed; falling back to text+buttons")
                await self._send_flattened_screen(target, out, on_choice)
                return

            view = self._view_for(out)
            embeds = build_embeds(out)
            if embeds:
                for i, embed in enumerate(embeds):
                    await target.send(embed=embed, view=view if i == len(embeds) - 1 else None)
            else:
                chunks = _chunks(out.content)
                for i, chunk in enumerate(chunks):
                    await target.send(chunk, view=view if i == len(chunks) - 1 else None)
        except discord.Forbidden:
            if out.private_to_discord_id is not None:
                await channel.send("🤫 มีข้อความลับส่งถึงเจ้า แต่ DM ถูกปิดอยู่ — เปิดรับ DM แล้วลองใหม่")
            else:
                raise

    async def _send_flattened_screen(self, target, out, on_choice):  # pragma: no cover
        """Render a declarative screen as plain text + a legacy ChoiceView. Used when
        Components V2 is disabled OR when a native V2 send failed — the safety net that
        keeps a scene from silently disappearing."""
        content, buttons, menus = flatten_screen(out.screen)
        view = ChoiceView([], on_choice, select_menus=menus, action_buttons=buttons)
        chunks = _chunks(content) or [""]
        for i, chunk in enumerate(chunks):
            await target.send(chunk, view=view if i == len(chunks) - 1 else None)

    def _make_on_choice(self, out: OutboundMessage):  # pragma: no cover
        """One closure both the V2 LayoutView and the legacy ChoiceView reuse: a
        clicked value re-enters normal routing as if typed. Identity comes from
        `interaction.user`, never the message — the single authorization seam."""

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

        return on_choice

    def _view_for(self, out: OutboundMessage):  # pragma: no cover
        if not out.choices and not out.select_menus and not out.action_buttons:
            return None
        return ChoiceView(
            out.choices,
            self._make_on_choice(out),
            select_menus=out.select_menus,
            action_buttons=out.action_buttons,
        )
