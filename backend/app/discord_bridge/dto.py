"""Plain DTOs at the Discord boundary (no ORM, no discord.py types)."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import MessageCategory


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
