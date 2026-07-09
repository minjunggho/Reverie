"""CombatEncounter + Combatant (§22).

Combat is structurally distinct (initiative + formal turns). The encounter is mostly
working state but is persisted so a crash mid-combat can resume. Combatants reference
Characters/NPCs by entity ref and carry their own combat-relevant numbers snapshotted
at combat start.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column


class CombatEncounter(Base, TimestampMixin):
    __tablename__ = "combat_encounters"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scene_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    round: Mapped[int] = mapped_column(Integer, default=1)
    turn_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|ended
    initiative_order: Mapped[list[str]] = mapped_column(JSON, default=list)  # combatant ids
    version: Mapped[int] = mapped_column(Integer, default=1)


class Combatant(Base, TimestampMixin):
    __tablename__ = "combatants"

    id: Mapped[str] = pk_column()
    encounter_id: Mapped[str] = fk_id("combat_encounters.id")
    entity_ref: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    initiative: Mapped[int] = mapped_column(Integer, default=0)

    hp: Mapped[int] = mapped_column(Integer, default=1)
    max_hp: Mapped[int] = mapped_column(Integer, default=1)
    ac: Mapped[int] = mapped_column(Integer, default=10)
    attack_bonus: Mapped[int] = mapped_column(Integer, default=0)
    damage_die: Mapped[int] = mapped_column(Integer, default=6)
    damage_bonus: Mapped[int] = mapped_column(Integer, default=0)

    is_pc: Mapped[bool] = mapped_column(Boolean, default=False)
    alive: Mapped[bool] = mapped_column(Boolean, default=True)
    has_action: Mapped[bool] = mapped_column(Boolean, default=True)
    has_reaction: Mapped[bool] = mapped_column(Boolean, default=True)
    conditions: Mapped[list[str]] = mapped_column(JSON, default=list)
