"""Components V2 adapter — a `ReverieScreen` becomes a `discord.ui.LayoutView`.

This is the ONLY module that knows both the engine's declarative screen model and
discord.py's V2 primitives. A `LayoutView` automatically carries the
``IS_COMPONENTS_V2`` message flag and must not be combined with plain content or a
legacy embed — so a V2 screen's every visible word lives in a Text Display here.

Interaction contract is unchanged from the legacy `ChoiceView`: a button re-enters
its ``value``; a single-select re-enters the chosen option's value; a multi-select
re-enters ``submit_value_template`` with the chosen values substituted — one
interaction, one re-validated re-entry. Nothing is trusted from the client beyond
the value we minted; the engine revalidates against authoritative state.

Discord limits are clamped here (option/label/description/placeholder lengths, five
buttons per row, a total text budget) so builders upstream stay declarative.
"""
from __future__ import annotations

from typing import Awaitable, Callable

import discord

from app.presentation.screen import (
    MAX_BUTTON_LABEL,
    MAX_BUTTONS_PER_ROW,
    MAX_OPTION_DESCRIPTION,
    MAX_OPTION_LABEL,
    MAX_OPTION_VALUE,
    MAX_PLACEHOLDER,
    MAX_SELECT_OPTIONS,
    MAX_TEXT_DISPLAY,
    VALUES_SEPARATOR,
    ButtonRow,
    ReverieScreen,
    ScreenButton,
    ScreenSelect,
    SectionBlock,
    SelectRow,
    SeparatorBlock,
    TextBlock,
)

OnChoice = Callable[[discord.Interaction, str], Awaitable[None]]

_STYLES: dict[str, discord.ButtonStyle] = {
    "secondary": discord.ButtonStyle.secondary,
    "primary": discord.ButtonStyle.primary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}


class ReverieLayoutView(discord.ui.LayoutView):
    """A native Components V2 view built from a `ReverieScreen`.

    All text is inside the single accent Container; controls re-enter routing via the
    supplied ``on_choice`` closure — the identical path the legacy quick-reply buttons
    use, so authorization and stale-state guards stay in one place.
    """

    def __init__(
        self, screen: ReverieScreen, on_choice: OnChoice, *, timeout: float = 900
    ) -> None:
        super().__init__(timeout=timeout)
        self._on_choice = on_choice
        self._text_budget = MAX_TEXT_DISPLAY
        container = discord.ui.Container(
            accent_colour=screen.accent if screen.accent is not None else None
        )
        for block in screen.blocks:
            self._add_block(container, block)
        self.add_item(container)

    # -- block dispatch ---------------------------------------------------------
    def _add_block(self, container: discord.ui.Container, block) -> None:
        if isinstance(block, TextBlock):
            self._add_text(container, block.content)
        elif isinstance(block, SectionBlock):
            self._add_section(container, block)
        elif isinstance(block, SeparatorBlock):
            container.add_item(discord.ui.Separator(
                visible=block.divider,
                spacing=(discord.SeparatorSpacing.large if block.large
                         else discord.SeparatorSpacing.small),
            ))
        elif isinstance(block, SelectRow):
            row = discord.ui.ActionRow()
            row.add_item(self._select(block.select))
            container.add_item(row)
        elif isinstance(block, ButtonRow):
            row = discord.ui.ActionRow()
            for spec in block.buttons[:MAX_BUTTONS_PER_ROW]:
                row.add_item(self._button(spec))
            container.add_item(row)

    def _add_text(self, container: discord.ui.Container, content: str) -> None:
        text = self._budgeted(content)
        if text:
            container.add_item(discord.ui.TextDisplay(text))

    def _add_section(self, container: discord.ui.Container, block: SectionBlock) -> None:
        text = self._budgeted(block.text)
        if not text:
            return
        if block.accessory is None:
            container.add_item(discord.ui.TextDisplay(text))
            return
        section = discord.ui.Section(accessory=self._button(block.accessory))
        section.add_item(discord.ui.TextDisplay(text))
        container.add_item(section)

    def _budgeted(self, content: str) -> str:
        """Keep total Text-Display characters within the per-message V2 budget."""
        content = content or ""
        if self._text_budget <= 0:
            return ""
        if len(content) > self._text_budget:
            content = content[: self._text_budget]
        self._text_budget -= len(content)
        return content

    # -- interactive items ------------------------------------------------------
    def _button(self, spec: ScreenButton) -> discord.ui.Button:
        button = discord.ui.Button(
            label=spec.label[:MAX_BUTTON_LABEL],
            style=_STYLES.get(spec.style, discord.ButtonStyle.secondary),
            disabled=spec.disabled,
        )
        value = spec.value

        async def _cb(interaction: discord.Interaction) -> None:
            await self._on_choice(interaction, value)

        button.callback = _cb
        return button

    def _select(self, spec: ScreenSelect) -> discord.ui.Select:
        options = [
            discord.SelectOption(
                label=option.label[:MAX_OPTION_LABEL],
                value=option.value[:MAX_OPTION_VALUE],
                description=(option.description or "")[:MAX_OPTION_DESCRIPTION] or None,
                default=option.default,
            )
            for option in spec.options[:MAX_SELECT_OPTIONS]
        ]
        max_values = max(1, min(spec.max_values, len(options))) if options else 1
        min_values = max(0, min(spec.min_values, max_values))
        select = discord.ui.Select(
            custom_id=spec.custom_id[:100],
            placeholder=spec.placeholder[:MAX_PLACEHOLDER],
            options=options,
            min_values=min_values,
            max_values=max_values,
        )
        template = spec.submit_value_template

        async def _cb(interaction: discord.Interaction) -> None:
            values = list(select.values)
            if template is not None:
                payload = template.replace("{values}", VALUES_SEPARATOR.join(values))
            else:
                payload = values[0] if values else ""
            await self._on_choice(interaction, payload)

        select.callback = _cb
        return select


def build_layout_view(
    screen: ReverieScreen, on_choice: OnChoice, *, timeout: float = 900
) -> ReverieLayoutView:
    """Public entry point: a `ReverieScreen` → a ready-to-send V2 LayoutView."""
    return ReverieLayoutView(screen, on_choice, timeout=timeout)
