"""Grant provenance, spells, resource state, and active effects. Canonical.

CharacterGrant answers "where did I get this?" for every significant capability
(mandate §26). CharacterSpell carries spell knowledge with provenance and prepared
state. ResourceState is the authoritative answer to "do I have uses left?" — the
LLM never answers that from narrative memory. ActiveEffect tracks ongoing effects;
at most one row per owner may require concentration.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column

GRANT_SOURCE_TYPES = (
    "CLASS", "CLASS_LEVEL", "SUBCLASS", "SPECIES", "BACKGROUND", "FEAT",
    "ITEM", "CAMPAIGN_HOMEBREW", "TEMPORARY_EFFECT",
)


class CharacterGrant(Base, TimestampMixin):
    __tablename__ = "character_grants"

    id: Mapped[str] = pk_column()
    character_id: Mapped[str] = fk_id("characters.id")
    grant_type: Mapped[str] = mapped_column(String(24))   # feature|trait|feat|skill|save|tool|language|resource
    key: Mapped[str] = mapped_column(String(80))          # e.g. "arcane_recovery", "darkvision"
    name_th: Mapped[str] = mapped_column(String(160), default="")
    source_type: Mapped[str] = mapped_column(String(24))  # one of GRANT_SOURCE_TYPES
    source_key: Mapped[str] = mapped_column(String(80), default="")  # e.g. "class:wizard"
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # e.g. {"resistances":["poison"]}


class CharacterSpell(Base, TimestampMixin):
    __tablename__ = "character_spells"
    __table_args__ = (
        UniqueConstraint("character_id", "spell_key", "kind", name="uq_char_spell"),
    )

    id: Mapped[str] = pk_column()
    character_id: Mapped[str] = fk_id("characters.id")
    spell_key: Mapped[str] = mapped_column(String(80))    # registry spell name
    kind: Mapped[str] = mapped_column(String(16))         # cantrip | book | known
    prepared: Mapped[bool] = mapped_column(Boolean, default=False)
    source_type: Mapped[str] = mapped_column(String(24), default="CLASS")
    source_key: Mapped[str] = mapped_column(String(80), default="")


class ResourceState(Base, TimestampMixin):
    __tablename__ = "resource_states"
    __table_args__ = (
        UniqueConstraint("character_id", "resource_id", name="uq_char_resource"),
    )

    id: Mapped[str] = pk_column()
    character_id: Mapped[str] = fk_id("characters.id")
    resource_id: Mapped[str] = mapped_column(String(80))  # registry definition_id
    current: Mapped[int] = mapped_column(Integer, default=0)
    max_value: Mapped[int] = mapped_column(Integer, default=0)


class ActiveEffect(Base, TimestampMixin):
    __tablename__ = "active_effects"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    character_id: Mapped[str] = fk_id("characters.id")    # the maintainer/owner
    name: Mapped[str] = mapped_column(String(120))
    spell_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    requires_concentration: Mapped[bool] = mapped_column(Boolean, default=False)
    targets: Mapped[list[str]] = mapped_column(JSON, default=list)  # entity refs
    started_game_time: Mapped[int] = mapped_column(Integer, default=0)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
