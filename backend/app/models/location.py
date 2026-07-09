"""Location — a place. Canonical.

Description layers separate what is obvious, what focused attention reveals, and
what is hidden — feeding the retrieval layer (a player prompt gets the obvious
layer; the hidden layer stays DM-scoped until discovered).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column


class Location(Base, TimestampMixin):
    __tablename__ = "locations"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    name: Mapped[str] = mapped_column(String(160))
    description_obvious: Mapped[str] = mapped_column(Text, default="")
    description_focused: Mapped[str] = mapped_column(Text, default="")
    description_hidden: Mapped[str] = mapped_column(Text, default="")
    connections: Mapped[list[str]] = mapped_column(JSON, default=list)
    contents: Mapped[list[str]] = mapped_column(JSON, default=list)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
