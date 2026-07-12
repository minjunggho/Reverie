"""The application-facing bridge the Discord bot calls.

No `discord.py` types cross this boundary inward, and no engine internals leak out
to the bot. DTOs are imported eagerly (many engine services build OutboundMessages);
the bridge classes are exposed lazily to avoid import cycles with the services they
orchestrate.
"""
from app.discord_bridge.dto import (
    ActionButton,
    BridgeResult,
    InboundAttachment,
    InboundMessage,
    OutboundMessage,
    SelectMenu,
    SelectOption,
)

__all__ = [
    "DiscordBridge",
    "AdminBridge",
    "is_admin_command",
    "InboundMessage",
    "InboundAttachment",
    "OutboundMessage",
    "BridgeResult",
    "SelectOption",
    "SelectMenu",
    "ActionButton",
]


def __getattr__(name: str):
    if name == "DiscordBridge":
        from app.discord_bridge.bridge import DiscordBridge

        return DiscordBridge
    if name in ("AdminBridge", "is_admin_command"):
        from app.discord_bridge import admin_bridge

        return getattr(admin_bridge, name)
    raise AttributeError(name)
