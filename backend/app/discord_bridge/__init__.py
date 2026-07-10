"""The application-facing bridge the Discord bot calls.

No `discord.py` types cross this boundary inward, and no engine internals leak out
to the bot. The bot converts a Discord event into an `InboundMessage`, calls
`DiscordBridge.handle_inbound`, and posts the returned `OutboundMessage`s.
"""
from app.discord_bridge.dto import BridgeResult, InboundMessage, OutboundMessage
from app.discord_bridge.bridge import DiscordBridge
from app.discord_bridge.admin_bridge import AdminBridge, is_admin_command

__all__ = [
    "DiscordBridge",
    "AdminBridge",
    "is_admin_command",
    "InboundMessage",
    "OutboundMessage",
    "BridgeResult",
]
