"""Event — the canonical append-only history of meaningful occurrences.

See `docs/event-model.md`. Events are written in the SAME transaction as the state
change they record. `seq` is a per-campaign monotonic ordering assigned by the
event service under the campaign row.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.db.base import Base, pk_column, fk_id
from app.models.enums import Visibility


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = pk_column()
    seq: Mapped[int] = mapped_column(Integer, index=True)
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scene_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    event_type: Mapped[str] = mapped_column(String(40), index=True)
    campaign_time: Mapped[int] = mapped_column(Integer, default=0)
    real_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    actor_entity: Mapped[str | None] = mapped_column(String(80), nullable=True)
    target_entities: Mapped[list[str]] = mapped_column(JSON, default=list)
    location_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    witnesses: Mapped[list[str]] = mapped_column(JSON, default=list)
    visibility: Mapped[str] = mapped_column(String(16), default=Visibility.PARTY.value)

    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    mechanical_changes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    narrative_significance: Mapped[int] = mapped_column(Integer, default=0)
