"""The Discord adapter — a thin edge over `app.discord_bridge`.

It contains NO game logic. It converts a discord.py message into an `InboundMessage`,
calls the bridge, and posts the returned `OutboundMessage`s. The engine never imports
discord.py; the bot never imports engine internals beyond the bridge DTOs.
"""
