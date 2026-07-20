"""Test helpers for reading the declarative Components-V2 screen model.

Migrated interactive screens carry a `ReverieScreen` on `OutboundMessage.screen`
instead of legacy `embed`/`select_menus`/`action_buttons`. These accessors let tests
assert semantically (a control's presence, an option's default, the re-entry value a
click would produce) without depending on discord.py rendering or brittle snapshots.
"""
from __future__ import annotations

from app.discord_bridge.dto import OutboundMessage
from app.presentation.screen import ReverieScreen, ScreenButton, ScreenSelect, VALUES_SEPARATOR


def screen_of(message: OutboundMessage) -> ReverieScreen:
    assert message.screen is not None, "message has no Components-V2 screen"
    return message.screen


def screen_text(message: OutboundMessage) -> str:
    """All visible text of the screen (its plain-text flattening)."""
    return message.screen.to_text() if message.screen is not None else (message.content or "")


def screen_text_from(screen: ReverieScreen) -> str:
    """The visible text of a bare `ReverieScreen` (not wrapped in a message)."""
    return screen.to_text()


def selects(message: OutboundMessage) -> list[ScreenSelect]:
    return screen_of(message).selects()


def buttons(message: OutboundMessage) -> list[ScreenButton]:
    return screen_of(message).buttons()


def button_by_label(message: OutboundMessage, label: str) -> ScreenButton:
    """First button whose label contains ``label`` (labels may carry emoji/paging)."""
    for button in buttons(message):
        if button.label == label or label in button.label:
            return button
    raise AssertionError(f"no button with label ~ {label!r}")


def button_by_action(message: OutboundMessage, action: str) -> ScreenButton:
    """First button whose re-entry value ends with a component ``:action``."""
    for button in buttons(message):
        if button.value.endswith(f":{action}") or f":{action}:" in button.value:
            return button
    raise AssertionError(f"no button for action {action!r}")


def option_values(message: OutboundMessage, index: int = 0) -> list[str]:
    return [o.value for o in selects(message)[index].options]


def default_values(message: OutboundMessage, index: int = 0) -> list[str]:
    return [o.value for o in selects(message)[index].options if o.default]


def multi_submit(message: OutboundMessage, values: list[str], index: int = 0) -> str:
    """The single re-entry text a multi-select would produce for ``values`` — the
    exact string the adapter builds from ``submit_value_template``."""
    select = selects(message)[index]
    template = select.submit_value_template
    assert template is not None, "select is not a multi-select (no submit template)"
    return template.replace("{values}", VALUES_SEPARATOR.join(values))


def has_screen(message: OutboundMessage) -> bool:
    return message.screen is not None
