"""NPC — a non-player character. Canonical.

The epistemic records (NPCKnowledge/Belief/Suspicion/Memory/Relationship) arrive in
Phase 11. Objective world truth is NEVER the same as what an NPC knows; the
retrieval layer only ever hands an NPC prompt what that NPC is allowed to use.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column


class NPC(Base, TimestampMixin):
    __tablename__ = "npcs"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    name: Mapped[str] = mapped_column(String(160))
    personality: Mapped[str] = mapped_column(Text, default="")
    voice_register: Mapped[str] = mapped_column(String(200), default="")
    goals: Mapped[list[str]] = mapped_column(JSON, default=list)
    current_location_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # attitude per entity ref, e.g. {"character:<id>": "neutral"}
    attitudes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    emotional_state: Mapped[str] = mapped_column(String(60), default="calm")
    # How this NPC communicates: SPOKEN|SLATE|SIGN|TELEPATHY|NONVERBAL|OTHER. Default
    # preserves every existing NPC's behavior unchanged (spoken dialogue).
    communication_mode: Mapped[str] = mapped_column(String(20), default="SPOKEN")
    # Same typed BeliefProfile payload used by Character. Imported canon and
    # generated proposals are reconciled by BeliefService, never by JSON merging.
    belief_profile: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, default=None
    )
