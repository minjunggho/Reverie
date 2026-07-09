"""Threat + ScheduledWorldEvent (§21). Canonical.

The world keeps moving. Threats are fronts with a goal and 0..100 progress that tick
on their own in-world schedule; scheduled events fire at a due game-time. Ticking is
ENGINE-OWNED (a domain service), never remembered by the LLM.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column


class Threat(Base, TimestampMixin):
    __tablename__ = "threats"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    name: Mapped[str] = mapped_column(String(160))
    goal: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="active")  # active|paused|resolved
    progress: Mapped[int] = mapped_column(Integer, default=0)          # 0..100
    next_action: Mapped[str] = mapped_column(Text, default="")
    scheduled_game_time: Mapped[int] = mapped_column(Integer, default=0)
    tick_amount: Mapped[int] = mapped_column(Integer, default=10)
    tick_interval: Mapped[int] = mapped_column(Integer, default=240)   # in-world minutes


class ScheduledWorldEvent(Base, TimestampMixin):
    __tablename__ = "scheduled_world_events"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    due_game_time: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(60), default="generic")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Whether characters can perceive it when it fires (drives AI narration).
    perceivable: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
