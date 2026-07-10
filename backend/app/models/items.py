"""ItemDefinition (reusable template) + InventoryEntry (a character's owned item).

Canonical ledger. Quantities/equipped state change only through InventoryService
inside a unit-of-work, paired with ITEM_GAINED / ITEM_LOST events.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column


class ItemDefinition(Base, TimestampMixin):
    __tablename__ = "item_definitions"

    id: Mapped[str] = pk_column()
    # Campaign-scoped definitions; NULL campaign = shared starter template.
    campaign_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40), default="gear")  # weapon|armor|gear|consumable|treasure
    description: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class InventoryEntry(Base, TimestampMixin):
    __tablename__ = "inventory_entries"

    id: Mapped[str] = pk_column()
    character_id: Mapped[str] = fk_id("characters.id")
    item_definition_id: Mapped[str] = fk_id("item_definitions.id")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    equipped: Mapped[bool] = mapped_column(Boolean, default=False)
