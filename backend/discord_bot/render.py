"""Discord renderer — maps the engine's presentation contract to Discord output.

One place owns the visual language: kinds → embed color/emoji/structure, choices →
button rows. The engine never imports this; tests assert on kinds+data upstream.
"""
from __future__ import annotations

from typing import Awaitable, Callable

import discord

from app.discord_bridge.dto import ActionButton, OutboundMessage, SelectMenu
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
    """Engine-described controls whose values re-enter normal message routing."""

    def __init__(
        self,
        choices: list[str],
        on_choice: Callable[[discord.Interaction, str], Awaitable[None]],
        *,
        select_menus: list[SelectMenu] | None = None,
        action_buttons: list[ActionButton] | None = None,
        timeout: float = 900,
    ) -> None:
        super().__init__(timeout=timeout)
        for menu in (select_menus or [])[:5]:
            if menu.options:
                self.add_item(self._select(menu, on_choice))
        for label in choices[:25]:
            self.add_item(self._button(ActionButton(label=label, value=label), on_choice))
        for button in (action_buttons or [])[:25]:
            self.add_item(self._button(button, on_choice))

    @staticmethod
    def _button(spec: ActionButton, on_choice):
        styles = {
            "secondary": discord.ButtonStyle.secondary,
            "primary": discord.ButtonStyle.primary,
            "success": discord.ButtonStyle.success,
            "danger": discord.ButtonStyle.danger,
        }
        btn = discord.ui.Button(
            label=spec.label[:80],
            style=styles[spec.style],
            disabled=spec.disabled,
        )

        async def _cb(interaction: discord.Interaction) -> None:
            await on_choice(interaction, spec.value)

        btn.callback = _cb
        return btn

    @staticmethod
    def _select(spec: SelectMenu, on_choice):
        if len(spec.custom_id) > 100:
            raise ValueError("Discord select custom_id exceeds 100 characters")
        too_long = [option.value for option in spec.options if len(option.value) > 100]
        if too_long:
            raise ValueError("Discord select option value exceeds 100 characters")
        options = [
            discord.SelectOption(
                label=option.label[:100],
                value=option.value,
                description=(option.description or "")[:100] or None,
                default=option.default,
            )
            for option in spec.options[:25]
        ]
        select = discord.ui.Select(
            custom_id=spec.custom_id,
            placeholder=spec.placeholder[:150],
            options=options,
            min_values=max(0, min(spec.min_values, len(options))),
            max_values=max(1, min(spec.max_values, len(options))),
        )

        async def _cb(interaction: discord.Interaction) -> None:
            await on_choice(interaction, select.values[0])

        select.callback = _cb
        return select


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
