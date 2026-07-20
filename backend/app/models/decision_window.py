"""Shared decision window — the new unit of resolution.

Replaces "one player message = one world turn" with "one shared decision window
produces one frozen set of intentions, resolved into one coherent world update."

A `DecisionWindow` is a server-authoritative state machine (see `WindowPhase`). Each
eligible actor owns exactly one `ActionSubmission` in the window (upserted by revision);
editing bumps the revision and clears Ready. When every required actor is Ready — or a
host forces it — the window freezes an immutable `frozen_snapshot` and the resolver
turns it into a `round_package` (persisted for replay/debug) and one combined scene.

Nothing here trusts chat-message order or client state: readiness, freezing, and
resolution are computed from these rows under optimistic-concurrency (`version`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column
from app.models.enums import (
    SubmissionValidation,
    SubmissionVisibility,
    WindowMode,
    WindowPhase,
)


class DecisionWindow(Base, TimestampMixin):
    __tablename__ = "decision_windows"
    __table_args__ = (
        # One live window per scene+round; re-opening the same round is idempotent.
        UniqueConstraint("scene_id", "round_id", name="uq_window_scene_round"),
    )

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    session_id: Mapped[str] = fk_id("sessions.id")
    scene_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    round_id: Mapped[int] = mapped_column(Integer, default=1)
    mode: Mapped[str] = mapped_column(String(16), default=WindowMode.NONCOMBAT.value)
    phase: Mapped[str] = mapped_column(String(24), default=WindowPhase.AWAITING_ACTIONS.value)

    # Character ids that MUST act (or explicitly pass) before the round can resolve.
    required_actor_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Characters excused this round (disconnected/AFK/host-excluded) — required minus
    # excused is the set the ready-gate waits on.
    excused_actor_ids: Mapped[list[str]] = mapped_column(JSON, default=list)

    # Policy snapshot taken when the window opened, so mid-round config edits can't
    # change the rules of a round already in flight.
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # The immutable freeze: every submission as it stood at lock time. Written once.
    frozen_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # The resolved round package (initiative, rolls, invalidations, fallbacks, damage,
    # conditions, environment/objective changes) — persisted for replay and debugging.
    round_package: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ActionSubmission(Base, TimestampMixin):
    __tablename__ = "action_submissions"
    __table_args__ = (
        # Exactly one submission per actor per window — submit/edit is an upsert.
        UniqueConstraint("window_id", "actor_id", name="uq_submission_window_actor"),
    )

    id: Mapped[str] = pk_column()
    window_id: Mapped[str] = fk_id("decision_windows.id")
    actor_id: Mapped[str] = mapped_column(String(32), index=True)

    # Bumped on every edit; Ready locks a specific revision, so a stale client cannot
    # ready an intention the player has since changed.
    revision: Mapped[int] = mapped_column(Integer, default=1)
    # Verbatim player text — preserved for narration (dialogue/monologue especially).
    raw_player_text: Mapped[str] = mapped_column(Text, default="")

    # Structured intent (parsed from raw text and/or supplied by the UI).
    dialogue: Mapped[str] = mapped_column(Text, default="")
    movement_intent: Mapped[bool] = mapped_column(Boolean, default=False)
    destination: Mapped[str] = mapped_column(String(200), default="")
    primary_action: Mapped[str] = mapped_column(Text, default="")
    action_target: Mapped[str] = mapped_column(String(200), default="")
    bonus_action: Mapped[str] = mapped_column(String(200), default="")
    bonus_target: Mapped[str] = mapped_column(String(200), default="")
    interaction: Mapped[str] = mapped_column(String(200), default="")
    reaction_intent: Mapped[str] = mapped_column(String(200), default="")
    condition: Mapped[str] = mapped_column(String(400), default="")
    fallback_action: Mapped[str] = mapped_column(Text, default="")
    fallback_target: Mapped[str] = mapped_column(String(200), default="")
    desired_tone: Mapped[str] = mapped_column(String(80), default="")
    declared_resource_use: Mapped[list[str]] = mapped_column(JSON, default=list)
    required_rolls: Mapped[list[dict]] = mapped_column(JSON, default=list)

    visibility: Mapped[str] = mapped_column(String(12), default=SubmissionVisibility.OPEN.value)
    validation_status: Mapped[str] = mapped_column(
        String(20), default=SubmissionValidation.PENDING.value)
    validation_errors: Mapped[list[str]] = mapped_column(JSON, default=list)

    # PASS = the player chose not to act this round (still counts as "ready").
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Last accepted idempotency key, so a duplicate submit/ready is a no-op.
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @property
    def is_ready(self) -> bool:
        return self.ready_at is not None or self.passed
