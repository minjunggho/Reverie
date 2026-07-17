"""Chapter — the middle of the progression hierarchy. Canonical.

The desired hierarchy is: campaign goal → chapter goal → active objective → immediate
task → leads. Reverie had the top (`Campaign.central_question`) and the bottom
(`Scene.purpose`) and nothing in between, so nothing could answer "what is the party
supposed to be doing right now" (docs/progression-audit.md, RC2).

A Chapter groups objectives under one goal and advances when they are RESOLVED — not
when they are *succeeded*. That distinction is deliberate: a chapter that waits for
success can be deadlocked forever by one failed check, which is exactly the "failed
check blocks the only route forward" symptom. A failed objective is a resolved
objective; the story moves on changed rather than stopping.

Objectives themselves are `Quest` (models/consequences.py) — it already had the state
machine, the progress field, and an event-recording upsert, and was simply never wired
to anything. This layer gives it a parent rather than duplicating it.

NOTE ON NAMING: `models/progression.py` is CHARACTER progression (levels, grants,
spell slots). This module is CAMPAIGN progression. They are unrelated.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column

# PENDING  — authored, not yet reached.
# ACTIVE   — the chapter the party is currently in. At most one per campaign.
# COMPLETED/ABANDONED — terminal.
CHAPTER_STATES = ("PENDING", "ACTIVE", "COMPLETED", "ABANDONED")


class Chapter(Base, TimestampMixin):
    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint("campaign_id", "key", name="uq_chapter_campaign_key"),
    )

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    key: Mapped[str] = mapped_column(String(80))          # stable per campaign
    name: Mapped[str] = mapped_column(String(200), default="")
    # What this chapter is trying to accomplish — player-safe, reaches the narrator.
    goal: Mapped[str] = mapped_column(Text, default="")
    # Authoring order. The next chapter by sort_order becomes ACTIVE when this one
    # completes; ties are broken by key so advancement is deterministic.
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    # DM-only framing for what this chapter is really about. Never player-facing.
    hidden_purpose: Mapped[str] = mapped_column(Text, default="")
    # A chapter the party may skip entirely without stalling the campaign — it is not
    # required for the chapter after it to open.
    optional: Mapped[bool] = mapped_column(Boolean, default=False)


# What learning a clue can DO. Each kind names an existing engine capability — a clue
# reveal is never a new mutation path, only a trigger for one that already exists.
#   location  — a place becomes routable (Location.discovery_state → KNOWN)
#   route     — an edge becomes usable (ConsequenceService.discover_route)
#   objective — an objective becomes known work (Quest UNKNOWN → DISCOVERED)
#   fact      — a world fact becomes party-visible (CampaignCanonRecord)
#   npc       — a person becomes someone the party knows to look for
#   secret    — points at an authored Secret (does NOT reveal it; reveal_secret does)
CLUE_REVEAL_KINDS = ("location", "route", "objective", "fact", "npc", "secret")


class Clue(Base, TimestampMixin):
    """A discoverable piece of information that CHANGES something when learned.

    Clues were `list[str]` on a Secret, on a Scene, and in main_story — free text, in
    three places, linked to nothing. So "the engine does not reliably know what clues
    unlock which destinations" was literally true: no field existed that could hold
    that edge, and a revealed clue was narrated and forgotten
    (docs/progression-audit.md, RC3).

    `reveals` is that missing edge: a list of {"kind": ..., "ref": ...} where kind is
    one of CLUE_REVEAL_KINDS. Learning the clue applies them through the existing
    consequence services, so a torn ledger page can actually open a route rather than
    just being read aloud.
    """

    __tablename__ = "clues"
    __table_args__ = (
        UniqueConstraint("campaign_id", "key", name="uq_clue_campaign_key"),
    )

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    key: Mapped[str] = mapped_column(String(80))
    # The authored fragment the party actually receives. This is the text the narrator
    # may surface verbatim — it is never generated.
    text: Mapped[str] = mapped_column(Text)

    # --- where it can be found (all optional; a clue may be findable many ways) ---
    location_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    npc_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # The authored Secret this clue is evidence for, if any. Learning the clue does NOT
    # reveal the secret — it points at it. Revealing stays on the reveal_secret path.
    secret_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    # --- what learning it does ----------------------------------------------------
    reveals: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # --- runtime state ------------------------------------------------------------
    discovered: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    discovered_game_time: Mapped[int] = mapped_column(Integer, default=0)
    # Importance for retrieval ranking when several clues could surface at once.
    importance: Mapped[int] = mapped_column(Integer, default=10)
