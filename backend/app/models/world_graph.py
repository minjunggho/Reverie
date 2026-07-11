"""LocationConnection — the authoritative travel graph edge + CampaignCanonRecord.

A connection is a directed edge the engine resolves natural movement against (the
free-form `Location.connections` JSON is superseded by this). CampaignCanonRecord is
the general lore + clue layer that complements the reused entity models (Secret / NPC
/ Threat / Location / KnowledgeRecord). See docs/world-canon.md.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column


class LocationConnection(Base, TimestampMixin):
    __tablename__ = "location_connections"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    from_location_id: Mapped[str] = fk_id("locations.id")
    to_location_id: Mapped[str] = fk_id("locations.id")
    # Player-facing exit label ("front door", "ประตูหน้า") + a coarse direction.
    label: Mapped[str] = mapped_column(String(120), default="")
    direction: Mapped[str] = mapped_column(String(40), default="")  # outside/uphill/up/down/...
    travel_minutes: Mapped[int] = mapped_column(Integer, default=0)
    obvious: Mapped[bool] = mapped_column(Boolean, default=True)     # visible without searching
    one_way: Mapped[bool] = mapped_column(Boolean, default=False)
    access_state: Mapped[str] = mapped_column(String(20), default="open")  # open|locked|blocked|hidden
    requirement: Mapped[str] = mapped_column(String(200), default="")


class CampaignCanonRecord(Base, TimestampMixin):
    __tablename__ = "campaign_canon_records"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    category: Mapped[str] = mapped_column(String(32), default="world_fact")  # world_fact|clue|history|religion|magic|politics|culture
    fact: Mapped[str] = mapped_column(Text)
    truth_status: Mapped[str] = mapped_column(String(20), default="TRUE")   # TRUE|FALSE_BELIEF|RUMOR
    visibility: Mapped[str] = mapped_column(String(16), default="PUBLIC")   # Visibility value
    provenance: Mapped[str] = mapped_column(String(24), default="IMPORTED_CANON")
    importance: Mapped[int] = mapped_column(Integer, default=10)            # 0..100 retrieval rank
    # Optional scoping to another entity (e.g. a clue that points at a Secret).
    scope_type: Mapped[str | None] = mapped_column(String(24), nullable=True)  # location|npc|secret|faction|region
    scope_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
