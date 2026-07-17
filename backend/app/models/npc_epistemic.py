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

from sqlalchemy import Boolean, Float, Index, Integer, String, Text, UniqueConstraint
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


# When an intention comes due.
#   ON_NEXT_MEETING — the next time this NPC and its subject are face to face.
#   AFTER_TIME      — at `trigger_game_time`, whether or not the party is present.
#                     This is the one that lets an NPC act while the party is elsewhere.
#   IMMEDIATELY     — on the NPC's next opportunity to act at all.
INTENTION_TRIGGERS = ("ON_NEXT_MEETING", "AFTER_TIME", "IMMEDIATELY")

# PENDING → FULFILLED (it happened) | ABANDONED (the NPC gave it up) |
#           EXPIRED (the world moved past it).
INTENTION_STATES = ("PENDING", "FULFILLED", "ABANDONED", "EXPIRED")


class NPCIntention(Base, TimestampMixin):
    """What an NPC MEANS TO DO — persisted, so it survives the turn that formed it.

    NPCDecisionService already derived follow-ups from suspicion and unresolved
    questions ("watch them closely", "call for help"), on an escalating engine-owned
    ladder. But they were recomputed every turn, rendered into one prompt, and thrown
    away. So an NPC could remember and could react when spoken to, and could never:
    carry a plan across turns, act while the party was elsewhere, or initiate anything.
    "NPC attitudes change numerically without changing behavior" was exact — the
    numbers persisted in NPCRelationship, the behavior they implied did not
    (docs/progression-audit.md, RC6).

    An intention is engine-derived and engine-owned. The model renders one into words;
    it never authors, edits, or retires one.
    """

    __tablename__ = "npc_intentions"
    __table_args__ = (
        # Recall is always "what does this NPC intend, and about whom".
        Index("ix_npc_intentions_npc_subject", "npc_id", "subject_ref"),
        # The world clock sweeps due intentions across all NPCs.
        Index("ix_npc_intentions_due", "state", "trigger", "trigger_game_time"),
        # One live intention of a kind per (npc, subject): re-deriving the same plan on
        # the next turn must not stack six copies of "watch them closely".
        UniqueConstraint("npc_id", "subject_ref", "kind", "state",
                         name="uq_npc_intention_live"),
    )

    id: Mapped[str] = pk_column()
    npc_id: Mapped[str] = fk_id("npcs.id")
    # Who the intention is ABOUT. NULL for a plan aimed at no one in particular (an
    # NPC pursuing its own goal), which is why this is not part of the FK.
    subject_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # A stable machine label ("WATCH", "QUESTION", "CALL_HELP", "PURSUE_GOAL") — what
    # the engine reasons about. Distinct from `description`, which is the player-facing
    # line the NPC would think in.
    kind: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text, default="")
    trigger: Mapped[str] = mapped_column(String(20), default="ON_NEXT_MEETING")
    trigger_game_time: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    # 0..100. Orders competing intentions — a cornered NPC calls for help before it
    # gets around to asking you about the ledger.
    urgency: Mapped[int] = mapped_column(Integer, default=10)
    # The memory that produced this intention, when there is one.
    source_memory_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
