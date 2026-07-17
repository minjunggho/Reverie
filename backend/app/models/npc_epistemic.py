"""NPC epistemic + relational records (§18). Canonical, DM/authorization-scoped.

These separate what is OBJECTIVELY true (KnowledgeRecord/Secret) from what a specific
NPC knows/believes/suspects. The retrieval layer only ever hands an NPC prompt rows
from THIS table for THAT npc — never objective truth the NPC has not learned.

`NPCFact` consolidates knowledge/belief/suspicion/rumor via `status`
(KnowledgeStatus). `NPCRelationship` holds per-entity multi-dimensional feeling.
`NPCMemory` is episodic: specific things a specific character DID to this NPC,
linked to the committed source event, retrieved to make behavior player-specific
and persistent across sessions.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, Integer, String, Text
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


# Bounded relationship dimensions (§7). Each is clamped to [-100, 100] by the
# service; `trust`/`attitude` are kept for back-compat with older callers/tests.
RELATIONSHIP_DIMENSIONS = (
    "familiarity", "trust", "affection", "respect",
    "fear", "anger", "suspicion", "obligation",
)


class NPCRelationship(Base, TimestampMixin):
    __tablename__ = "npc_relationships"

    id: Mapped[str] = pk_column()
    npc_id: Mapped[str] = fk_id("npcs.id")
    entity_ref: Mapped[str] = mapped_column(String(80))
    # Back-compat coarse fields.
    attitude: Mapped[str] = mapped_column(String(40), default="neutral")
    trust: Mapped[int] = mapped_column(Integer, default=0)
    # Multi-dimensional feeling toward THIS specific character.
    familiarity: Mapped[int] = mapped_column(Integer, default=0)
    affection: Mapped[int] = mapped_column(Integer, default=0)
    respect: Mapped[int] = mapped_column(Integer, default=0)
    fear: Mapped[int] = mapped_column(Integer, default=0)
    anger: Mapped[int] = mapped_column(Integer, default=0)
    suspicion: Mapped[int] = mapped_column(Integer, default=0)
    obligation: Mapped[int] = mapped_column(Integer, default=0)
    current_stance: Mapped[str] = mapped_column(String(40), default="neutral")
    last_interaction_event_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class NPCMemory(Base, TimestampMixin):
    __tablename__ = "npc_memories"
    __table_args__ = (
        # Recall is always "this NPC's memories of this subject".
        Index("ix_npc_memories_npc_subject", "npc_id", "subject_ref"),
    )

    id: Mapped[str] = pk_column()
    npc_id: Mapped[str] = fk_id("npcs.id")
    # The character/entity this memory is ABOUT (whose action it records).
    subject_ref: Mapped[str] = mapped_column(String(80))
    event_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    memory_type: Mapped[str] = mapped_column(String(24), default="INTERACTION")
    summary: Mapped[str] = mapped_column(Text, default="")
    importance: Mapped[int] = mapped_column(Integer, default=10)      # 0..100
    emotional_valence: Mapped[int] = mapped_column(Integer, default=0)  # -3..3
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    witnessed_directly: Mapped[bool] = mapped_column(Boolean, default=True)
    source_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    location_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    game_time: Mapped[int] = mapped_column(Integer, default=0)
    last_recalled_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # What this NPC still wants explained about the memory ("why were you reaching
    # for my map?"). This is what survives a change of subject: an unanswered
    # question is a thread the NPC is still pulling on, so a new topic cannot quietly
    # retire it. Empty for memories that ask nothing.
    open_question: Mapped[str] = mapped_column(Text, default="")
    # Whether the question has been ADDRESSED — not whether the NPC is satisfied. A
    # believed excuse resolves the question while the memory, and the damage it did to
    # trust, remain on the record.
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
