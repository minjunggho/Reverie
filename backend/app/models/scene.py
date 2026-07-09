"""Scene — the current interaction context. Mostly WORKING state.

A scene is distilled into canonical Events on transition; it is not the history of
record. It carries an optimistic-concurrency `version` for the same reason Session
does.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column
from app.models.enums import SceneMode, SceneStatus


class Scene(Base, TimestampMixin):
    __tablename__ = "scenes"

    id: Mapped[str] = pk_column()
    session_id: Mapped[str] = fk_id("sessions.id")
    location_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    mode: Mapped[str] = mapped_column(String(16), default=SceneMode.EXPLORATION.value)
    purpose: Mapped[str] = mapped_column(String(400), default="")
    dramatic_question: Mapped[str] = mapped_column(String(400), default="")
    tension: Mapped[int] = mapped_column(Integer, default=0)

    participants: Mapped[list[str]] = mapped_column(JSON, default=list)
    visible_entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    relevant_object_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    immediate_threat_ids: Mapped[list[str]] = mapped_column(JSON, default=list)

    # A pending committed action awaiting clarification (CLARIFICATION_REQUIRED).
    pending_action_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pending_action: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    scene_start_game_time: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default=SceneStatus.ACTIVE.value)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
