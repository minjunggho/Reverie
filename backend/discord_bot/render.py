"""Discord renderer — maps the engine's presentation contract to Discord output.

One place owns the visual language: kinds → embed color/emoji/structure, choices →
button rows. The engine never imports this; tests assert on kinds+data upstream.
"""
from __future__ import annotations

from typing import Awaitable, Callable

import discord

from app.discord_bridge.dto import OutboundMessage
from app.presentation import KIND_STYLE, MessageKind

DISCORD_LIMIT = 2000
EMBED_DESC_LIMIT = 4000


def _chunks(text: str, size: int = 1900) -> list[str]:
    """Split long text on line boundaries (fallback plain-text path)."""
    if len(text) <= size:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > size:
            if cur:
                out.append(cur)
            while len(line) > size:
                out.append(line[:size])
                line = line[size:]
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


class ChoiceView(discord.ui.View):
    """Quick-reply buttons. A click is fed back as if the user typed the label."""

    def __init__(
        self,
        choices: list[str],
        on_choice: Callable[[discord.Interaction, str], Awaitable[None]],
        timeout: float = 900,
    ) -> None:
        super().__init__(timeout=timeout)
        for label in choices[:25]:
            self.add_item(self._button(label, on_choice))

    @staticmethod
    def _button(label: str, on_choice):
        btn = discord.ui.Button(label=label[:80], style=discord.ButtonStyle.secondary)

        async def _cb(interaction: discord.Interaction) -> None:
            await on_choice(interaction, label)

        btn.callback = _cb
        return btn


def _field_lines(data: dict, key: str) -> str | None:
    v = data.get(key)
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return "\n".join(str(x) for x in v) or None
    return str(v)


def build_embed(msg: OutboundMessage) -> discord.Embed | None:
    """Render a kinded message as an embed. Returns None for plain-text messages."""
    if msg.kind is None:
        return None
    emoji, color = KIND_STYLE.get(msg.kind, ("", 0x999999))
    title = f"{emoji} {msg.title}" if msg.title else (emoji or None)
    desc = (msg.content or "")[:EMBED_DESC_LIMIT]
    embed = discord.Embed(title=title, description=desc or None, colour=color)

    d = msg.data or {}
    # Common structured fields, rendered consistently across kinds.
    if roll := d.get("roll_line"):
        embed.add_field(name="🎲", value=str(roll)[:1024], inline=False)
    if decision := d.get("decision_prompt"):
        embed.add_field(name="—", value=f"*{decision}*"[:1024], inline=False)
    if fields := d.get("fields"):  # list of {"name":…, "value":…, "inline":bool}
        for f in list(fields)[:23]:
            embed.add_field(
                name=str(f.get("name", "​"))[:256],
                value=str(f.get("value", "​"))[:1024],
                inline=bool(f.get("inline", False)),
            )
    if footer := d.get("footer"):
        embed.set_footer(text=str(footer)[:2048])
    return embed
