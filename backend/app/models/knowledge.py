"""KnowledgeRecord + Secret — information visibility & provenance. Canonical.

These back the retrieval-layer authorization (`app/knowledge`). Secret protection is
STRUCTURAL: a `DM_ONLY` secret physically cannot be selected into a player-facing
context because the retrieval query filters on visibility.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column
from app.models.enums import Visibility


class KnowledgeRecord(Base, TimestampMixin):
    __tablename__ = "knowledge_records"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    fact: Mapped[str] = mapped_column(Text)
    truth_value: Mapped[bool] = mapped_column(Boolean, default=True)
    visibility: Mapped[str] = mapped_column(String(16), default=Visibility.PARTY.value)
    # provenance: {"observed_by": [...], "told": [...], "rumor": [...],
    #              "believes": [...], "suspects": [...]}
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Secret(Base, TimestampMixin):
    __tablename__ = "secrets"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    fact: Mapped[str] = mapped_column(Text)
    # A true fact hidden from some/all players. DM-scoped by default.
    visibility: Mapped[str] = mapped_column(String(16), default=Visibility.DM_ONLY.value)
    # who may see it, e.g. {"characters": [...], "npcs": [...]}
    visibility_map: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    revealed: Mapped[bool] = mapped_column(Boolean, default=False)
