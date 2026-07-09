"""ResolvedContext — the identity/state resolution passed to routers and the pipeline.

Carries plain ids (not ORM objects) so it can safely cross transaction boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.discord_bridge.dto import InboundMessage


@dataclass
class ResolvedContext:
    inbound: InboundMessage
    campaign_id: str
    member_id: str
    session_id: str | None = None
    character_id: str | None = None
    processed_message_id: str | None = None
    # A pending clarification owned by THIS member, if the next message resolves it.
    pending_action: dict | None = None

    @property
    def channel_id(self) -> str:
        return self.inbound.channel_id
