"""CharacterDraft — the state of one player's guided character-creation conversation.

Working state (not history of record). One ACTIVE draft per member; the member's
plain messages route into the creation flow while a draft is active, so creation
feels like a conversation, not a form. Deleted/closed on reveal or cancel.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column


class CharacterDraft(Base, TimestampMixin):
    __tablename__ = "character_drafts"
    __table_args__ = (
        # DB-level guarantee: at most ONE active draft per member per campaign,
        # even across processes. Closed drafts (DONE/CANCELLED) are unconstrained.
        Index(
            "uq_character_drafts_active_member",
            "campaign_id",
            "member_id",
            unique=True,
            sqlite_where=text("status = 'ACTIVE'"),
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    member_id: Mapped[str] = fk_id("campaign_members.id")
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")  # ACTIVE|DONE|CANCELLED
    # Conversation step counter (bounded — the flow always converges).
    step: Mapped[int] = mapped_column(Integer, default=0)
    # Optimistic-concurrency stamp. Every save is a compare-and-update on this
    # value, so two racing writers (e.g. two processes handling a double-delivered
    # Discord interaction) can never silently overwrite each other's selections.
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Accumulated hook fields: concept, origin, desire, fear, flaw, connection,
    # appearance, name + proposed_class.
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
