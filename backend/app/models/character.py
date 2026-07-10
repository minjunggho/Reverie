"""Character — a player character. ALL mechanical values are engine-authoritative."""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column


class Character(Base, TimestampMixin):
    __tablename__ = "characters"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    owner_member_id: Mapped[str] = fk_id("campaign_members.id")

    name: Mapped[str] = mapped_column(String(120))
    ancestry: Mapped[str] = mapped_column(String(60), default="human")
    char_class: Mapped[str] = mapped_column(String(60), default="fighter")

    # Ability scores (3..20 in the supported subset).
    str_score: Mapped[int] = mapped_column("str_score", Integer, default=10)
    dex_score: Mapped[int] = mapped_column("dex_score", Integer, default=10)
    con_score: Mapped[int] = mapped_column("con_score", Integer, default=10)
    int_score: Mapped[int] = mapped_column("int_score", Integer, default=10)
    wis_score: Mapped[int] = mapped_column("wis_score", Integer, default=10)
    cha_score: Mapped[int] = mapped_column("cha_score", Integer, default=10)

    # Proficiencies: list of skill names from the supported skill map.
    proficiencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    proficiency_bonus: Mapped[int] = mapped_column(Integer, default=2)

    hp: Mapped[int] = mapped_column(Integer, default=10)
    max_hp: Mapped[int] = mapped_column(Integer, default=10)
    ac: Mapped[int] = mapped_column(Integer, default=10)
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)

    conditions: Mapped[list[str]] = mapped_column(JSON, default=list)
    resources: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Narrative hooks — what makes this character USABLE by the DM engine.
    # Keys: concept, origin, desire, fear, flaw, connection, appearance (Thai text).
    # Scene/opening context builders retrieve these to create character-relevant
    # opportunities; they are data for the DM, never auto-plot.
    hooks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    appearance: Mapped[str] = mapped_column(Text, default="")

    def ability_score(self, ability: str) -> int:
        return int(getattr(self, f"{ability.lower()}_score"))
