"""Scene — the current interaction context. Mostly WORKING state.

A scene is distilled into canonical Events on transition; it is not the history of
record. It carries an optimistic-concurrency `version` for the same reason Session
does.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String
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

    # A pending committed action awaiting clarification OR a player dice click
    # (pending_action["kind"]: "clarification" | "check").
    pending_action_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pending_action: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Authored clue fragments that MAY surface in this scene (e.g. on a failed
    # check). The LLM can time a reveal but never author one: reveal_fragment
    # deltas are validated against this list.
    allowed_clues: Mapped[list[str]] = mapped_column(JSON, default=list)

    scene_start_game_time: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default=SceneStatus.ACTIVE.value)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Spotlight awareness (NOT turn order): keeps quiet characters from vanishing
    # from the DM's awareness. {"last_actor": ref, "action_counts": {ref: n}}.
    # Presence != participation != spotlight != turn order (docs/multiplayer-identity.md).
    spotlight: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Consecutive committed actions that changed nothing the campaign tracks — no clue
    # found, no objective moved, no place reached, no world state altered. Reset by any
    # real progress. This is how the engine knows the party is going in circles without
    # asking the narrator's opinion of the fiction, and it is what lets the world push
    # back instead of waiting forever (docs/progression-audit.md, RC5).
    low_progress_turns: Mapped[int] = mapped_column(Integer, default=0)
    # Whether the scene has already done what it was opened to do. A scene whose
    # purpose is spent should hand off, not run until someone types 'leave'.
    purpose_satisfied: Mapped[bool] = mapped_column(Boolean, default=False)
