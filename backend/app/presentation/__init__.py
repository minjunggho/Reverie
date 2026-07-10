"""Presentation vocabulary — the engine-side contract for how output is STRUCTURED.

The engine never touches Discord. It tags every outbound message with a
`MessageKind` and structured `data`; the Discord adapter (`discord_bot/render.py`)
decides embeds/colors/components. Tests assert on kind + data, not on markup.
"""
from app.presentation.kinds import KIND_STYLE, MessageKind

__all__ = ["MessageKind", "KIND_STYLE"]
