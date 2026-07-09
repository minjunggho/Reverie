"""Session — one play session. Canonical.

`version` is an explicit optimistic-concurrency column. The engine bumps it on
meaningful state transitions and guards conditional updates on it (see
`app.services.concurrency`), so two stale writers cannot both win.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column
from app.models.enums import ActivePlayState, SessionStatus


class Session(Base, TimestampMixin):
    __tablename__ = "sessions"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default=SessionStatus.PREPARATION.value)
    active_play_state: Mapped[str] = mapped_column(
        String(28), default=ActivePlayState.TABLE_OPEN.value
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attendance: Mapped[list[str]] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
