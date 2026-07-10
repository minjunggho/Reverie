"""Plain DTOs at the Discord boundary (no ORM, no discord.py types).

`OutboundMessage` carries a presentation `kind` + structured `data` so the adapter
can render embeds/components. `content` remains the plain-text fallback — every
message must read fine as text alone. `choices` are quick-reply options; when the
player clicks one, the adapter feeds the choice value back through the normal
inbound path as if it had been typed (uniform, testable).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.enums import MessageCategory
from app.presentation import MessageKind


@dataclass(frozen=True)
class InboundMessage:
    """A message the bot received in a game channel, reduced to primitives."""
    discord_message_id: str
    guild_id: str
    channel_id: str
    author_discord_id: str
    author_display_name: str
    content: str
    is_bot: bool = False


@dataclass
class OutboundMessage:
    """A message the engine wants the bot to post."""
    channel_id: str
    content: str
    # Reserved: send privately to a user (e.g. player-only info). Bot honours it.
    private_to_discord_id: str | None = None
    # Presentation contract (adapter renders; engine only structures).
    kind: MessageKind | None = None
    title: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    # Quick-reply options (label == value fed back as typed text on click).
    choices: list[str] = field(default_factory=list)


@dataclass
class BridgeResult:
    """Outcome of handling one inbound message."""
    handled: bool
    duplicate: bool = False
    category: MessageCategory | None = None
    responses: list[OutboundMessage] = field(default_factory=list)
    # True only when a committed action mutated canonical state.
    state_mutated: bool = False
    note: str = ""
