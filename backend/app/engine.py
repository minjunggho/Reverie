"""Engine assembly — wire the bridge, router, and committed pipeline together.

This is the single composition root the Discord bot (or a test) uses to obtain a
ready `DiscordBridge`. It keeps wiring in one place so handlers stay thin.
"""
from __future__ import annotations

from app.ai.llm import LLMProvider, get_provider
from app.core.randomness import Randomness, SystemRandomness
from app.db.session import Database, get_database
from app.discord_bridge import DiscordBridge
from app.orchestration import (
    CommittedActionPipeline,
    MessageRouter,
    SessionSerializer,
)


def build_bridge(
    db: Database,
    *,
    provider: LLMProvider | None = None,
    rng: Randomness | None = None,
    serializer: SessionSerializer | None = None,
) -> DiscordBridge:
    provider = provider or get_provider()
    rng = rng or SystemRandomness()
    router = MessageRouter(db, provider)
    pipeline = CommittedActionPipeline(db, provider, rng)
    return DiscordBridge(
        db, router=router, pipeline=pipeline, serializer=serializer or SessionSerializer()
    )


def build_default_bridge() -> DiscordBridge:
    """Production wiring from settings (used by the live bot)."""
    return build_bridge(get_database())
