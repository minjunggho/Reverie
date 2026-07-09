"""ProcessedMessage — idempotency + per-action processing state. Canonical (operational).

Keyed by `discord_message_id`. Reprocessing the same Discord message resumes from
its recorded `stage` and never double-applies effects (see error-recovery, §32).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, pk_column
from app.models.enums import ProcessingStage


class ProcessedMessage(Base, TimestampMixin):
    __tablename__ = "processed_messages"

    id: Mapped[str] = pk_column()
    discord_message_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    campaign_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    stage: Mapped[str] = mapped_column(String(16), default=ProcessingStage.RECEIVED.value)
    category: Mapped[str | None] = mapped_column(String(24), nullable=True)
    pending_action_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Cached result so a redelivered message can return the same response.
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
