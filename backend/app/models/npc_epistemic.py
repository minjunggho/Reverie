"""NPC epistemic + relational records (§18). Canonical, DM/authorization-scoped.

These separate what is OBJECTIVELY true (KnowledgeRecord/Secret) from what a specific
NPC knows/believes/suspects. The retrieval layer only ever hands an NPC prompt rows
from THIS table for THAT npc — never objective truth the NPC has not learned.

`NPCFact` consolidates knowledge/belief/suspicion/rumor/memory via `status`
(KnowledgeStatus). `NPCRelationship` holds per-entity attitude + trust.
"""
from __future__ import annotations

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column
from app.models.enums import KnowledgeStatus


class NPCFact(Base, TimestampMixin):
    __tablename__ = "npc_facts"

    id: Mapped[str] = pk_column()
    npc_id: Mapped[str] = fk_id("npcs.id")
    subject: Mapped[str] = mapped_column(String(200))   # entity ref or topic
    fact: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=KnowledgeStatus.KNOWS.value)
    source: Mapped[str] = mapped_column(String(200), default="")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)


class NPCRelationship(Base, TimestampMixin):
    __tablename__ = "npc_relationships"

    id: Mapped[str] = pk_column()
    npc_id: Mapped[str] = fk_id("npcs.id")
    entity_ref: Mapped[str] = mapped_column(String(80))
    attitude: Mapped[str] = mapped_column(String(40), default="neutral")
    trust: Mapped[int] = mapped_column(Integer, default=0)
