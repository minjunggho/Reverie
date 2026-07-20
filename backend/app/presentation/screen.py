"""A declarative, framework-neutral screen model for interactive UI (§Components-V2).

The engine describes *what* a screen contains — header, instruction, status, a
selection control, a preview, navigation — as an ordered tuple of blocks. It never
names a Discord type. Two adapters consume this one model:

* the Components V2 renderer (`discord_bot/components_v2.py`) maps it to a
  `discord.ui.LayoutView` (Container → Section/TextDisplay/Separator/ActionRow), and
* the legacy fallback flattens it to plain text + a `ChoiceView`.

Because the model is pure data, a screen is fully reproducible from stored state and
is asserted against directly in tests (semantic assertions, not payload snapshots).

Interactive values re-enter the normal inbound route exactly like the legacy
`choices` contract: a button/option carries the text the engine will re-validate.
A multi-select instead submits ONE re-entry built from `submit_value_template`, so a
whole selection changes in a single interaction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union

ButtonStyle = Literal["secondary", "primary", "success", "danger"]

# Discord limits (verified against the current component spec; the renderer enforces
# them so a builder can stay declarative and readable).
MAX_SELECT_OPTIONS = 25
MAX_OPTION_LABEL = 100
MAX_OPTION_VALUE = 100
MAX_OPTION_DESCRIPTION = 100
MAX_PLACEHOLDER = 150
MAX_BUTTON_LABEL = 80
MAX_BUTTONS_PER_ROW = 5
MAX_TEXT_DISPLAY = 4000

# Sentinel the renderer replaces with the comma-joined chosen option values.
VALUES_TOKEN = "{values}"
VALUES_SEPARATOR = ","


@dataclass(frozen=True)
class ScreenButton:
    """A button whose ``value`` re-enters the normal inbound route on click."""

    label: str
    value: str
    style: ButtonStyle = "secondary"
    disabled: bool = False


@dataclass(frozen=True)
class ScreenOption:
    """One option in a select control."""

    label: str
    value: str
    description: str | None = None
    default: bool = False


@dataclass(frozen=True)
class ScreenSelect:
    """A select control.

    Single-select (``max_values == 1``) re-enters the chosen option's ``value``,
    exactly like a quick-reply button. Multi-select (``max_values > 1``) instead
    re-enters ``submit_value_template`` with :data:`VALUES_TOKEN` replaced by the
    chosen option values joined on :data:`VALUES_SEPARATOR` — so the engine receives
    the complete new selection in one interaction and revalidates it authoritatively.
    """

    custom_id: str
    placeholder: str
    options: tuple[ScreenOption, ...]
    min_values: int = 1
    max_values: int = 1
    submit_value_template: str | None = None

    @property
    def is_multi(self) -> bool:
        return self.max_values > 1 or self.submit_value_template is not None


# ---- layout blocks -------------------------------------------------------------------

@dataclass(frozen=True)
class TextBlock:
    """A run of markdown text (a Text Display)."""

    content: str


@dataclass(frozen=True)
class SectionBlock:
    """Text paired with a single trailing accessory button (a Section).

    Used for a status line or an option preview that carries its own action (e.g.
    an inspect button). With no accessory it renders as a plain Text Display.
    """

    text: str
    accessory: ScreenButton | None = None


@dataclass(frozen=True)
class SeparatorBlock:
    """A horizontal rule used sparingly to group regions for scanning."""

    divider: bool = True
    large: bool = False


@dataclass(frozen=True)
class SelectRow:
    """An action row holding exactly one select control."""

    select: ScreenSelect


@dataclass(frozen=True)
class ButtonRow:
    """An action row of up to five buttons (navigation / confirmation)."""

    buttons: tuple[ScreenButton, ...]


Block = Union[TextBlock, SectionBlock, SeparatorBlock, SelectRow, ButtonRow]


@dataclass(frozen=True)
class ReverieScreen:
    """An ordered, self-contained description of one interactive screen."""

    blocks: tuple[Block, ...] = ()
    accent: int | None = None

    # -- text fallback ----------------------------------------------------------
    def to_text(self) -> str:
        """Flatten to plain text so the screen still reads fine without components.

        Only text-bearing blocks contribute; controls surface separately as a
        `ChoiceView`. A large separator becomes a blank line for breathing room.
        """
        parts: list[str] = []
        for block in self.blocks:
            if isinstance(block, TextBlock):
                if block.content.strip():
                    parts.append(block.content.rstrip())
            elif isinstance(block, SectionBlock):
                if block.text.strip():
                    parts.append(block.text.rstrip())
            elif isinstance(block, SeparatorBlock) and block.large:
                parts.append("")
        return "\n".join(parts).strip()

    # -- control extraction (used by both adapters) -----------------------------
    def selects(self) -> list[ScreenSelect]:
        return [b.select for b in self.blocks if isinstance(b, SelectRow)]

    def buttons(self) -> list[ScreenButton]:
        found: list[ScreenButton] = []
        for block in self.blocks:
            if isinstance(block, ButtonRow):
                found.extend(block.buttons)
            elif isinstance(block, SectionBlock) and block.accessory is not None:
                found.append(block.accessory)
        return found


@dataclass
class ScreenBuilder:
    """Small ordered accumulator so builders read top-to-bottom like the screen."""

    accent: int | None = None
    _blocks: list[Block] = field(default_factory=list)

    def block(self, block: Block) -> "ScreenBuilder":
        self._blocks.append(block)
        return self

    def text(self, content: str) -> "ScreenBuilder":
        self._blocks.append(TextBlock(content))
        return self

    def section(self, text: str, accessory: ScreenButton | None = None) -> "ScreenBuilder":
        self._blocks.append(SectionBlock(text, accessory))
        return self

    def separator(self, *, large: bool = False) -> "ScreenBuilder":
        self._blocks.append(SeparatorBlock(large=large))
        return self

    def select(self, select: ScreenSelect) -> "ScreenBuilder":
        self._blocks.append(SelectRow(select))
        return self

    def buttons(self, *buttons: ScreenButton) -> "ScreenBuilder":
        kept = [b for b in buttons if b is not None]
        if kept:
            self._blocks.append(ButtonRow(tuple(kept)))
        return self

    def build(self) -> ReverieScreen:
        return ReverieScreen(blocks=tuple(self._blocks), accent=self.accent)
