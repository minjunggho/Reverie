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

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
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
