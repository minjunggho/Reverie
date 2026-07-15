"""Persistent world-consequence records (§11–13). Canonical.

Player actions leave durable marks the world remembers across sessions and restart:
crimes (whose *perceived* / *identified* / *reported* facts are tracked separately),
reputations across many social scopes, factions/fronts that advance on their own
schedule, quests, and rumors that spread through the world over TIME rather than
instantly.

Nothing here invents authoritative combat numbers — injury/HP still come only from the
dice path. These rows record CONSEQUENCE and KNOWLEDGE, not mechanics.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column

# Social reach of a reputation or crime (§12). Information does not travel instantly:
# a deed known INDIVIDUALLY (one victim) is not the same as one known across a REGION.
REPUTATION_SCOPES = (
    "INDIVIDUAL", "LOCAL", "SETTLEMENT", "FACTION", "REGION",
    "PROFESSION", "UNDERWORLD", "RELIGIOUS", "POLITICAL",
)

# The ladder a rumor climbs one rung at a time as it spreads.
RUMOR_LADDER = ("LOCAL", "SETTLEMENT", "REGION", "POLITICAL")

QUEST_STATES = (
    "UNKNOWN", "DISCOVERED", "ACTIVE", "BLOCKED",
    "COMPLETED", "FAILED", "ABANDONED", "TRANSFORMED",
)


class Faction(Base, TimestampMixin):
    """A front/power with its own goal that advances on its own schedule (§13).

    A faction keeps moving whether or not the party interferes: the world clock fires
    a scheduled ``faction_action`` at ``scheduled_game_time`` that advances progress
    and reschedules the next beat.
    """

    __tablename__ = "factions"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    name: Mapped[str] = mapped_column(String(160))
    goal: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="active")  # active|paused|resolved|destroyed
    leader_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)              # 0..100 toward its goal
    disposition_to_party: Mapped[int] = mapped_column(Integer, default=0)  # -100..100
    resources: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    territory: Mapped[list[str]] = mapped_column(JSON, default=list)        # location ids held
    relationships: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # other_faction_id -> stance
    knowledge: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)   # what the faction has learned
    plans: Mapped[str] = mapped_column(Text, default="")
    # When the faction next acts on its own timeline (in-world minutes). Engine-owned.
    scheduled_game_time: Mapped[int] = mapped_column(Integer, default=0)


class Reputation(Base, TimestampMixin):
    """How a subject (usually a character) is regarded within ONE social scope.

    The same person can be a hero LOCALLY and wanted by the city's UNDERWORLD; each is a
    distinct row keyed by ``(subject_ref, scope, scope_ref)``.
    """

    __tablename__ = "reputations"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    subject_ref: Mapped[str] = mapped_column(String(80))     # entity ref, e.g. character:<id>
    scope: Mapped[str] = mapped_column(String(20), default="LOCAL")
    scope_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)  # which settlement/faction/...
    value: Mapped[int] = mapped_column(Integer, default=0)   # -100..100
    wanted: Mapped[bool] = mapped_column(Boolean, default=False)


class CrimeRecord(Base, TimestampMixin):
    """A recorded offence. *Perceived* / *identified* / *reported* are SEPARATE facts:
    a witness may know an attack happened without knowing who did it, so
    ``perpetrator_ref`` stays NULL until someone actually identifies the actor.
    """

    __tablename__ = "crime_records"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    crime_type: Mapped[str] = mapped_column(String(40))     # assault|theft|murder|trespass|...
    victim_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    perpetrator_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)  # NULL until identified
    location_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    game_time: Mapped[int] = mapped_column(Integer, default=0)
    perceived: Mapped[bool] = mapped_column(Boolean, default=True)    # someone knows it happened
    identified: Mapped[bool] = mapped_column(Boolean, default=False)  # someone can name the actor
    reported: Mapped[bool] = mapped_column(Boolean, default=False)    # authorities/faction informed
    status: Mapped[str] = mapped_column(String(20), default="open")   # open|reported|solved|closed
    witnesses: Mapped[list[str]] = mapped_column(JSON, default=list)  # entity refs who perceived it
    # Idempotency: one source action records at most one crime.
    source_event_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class Quest(Base, TimestampMixin):
    """A quest/objective whose state and progress persist across sessions."""

    __tablename__ = "quests"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    key: Mapped[str] = mapped_column(String(80))            # stable per campaign
    name: Mapped[str] = mapped_column(String(200), default="")
    state: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    progress: Mapped[int] = mapped_column(Integer, default=0)   # 0..100
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # leads/clues/hidden truth


class Rumor(Base, TimestampMixin):
    """A piece of (possibly false) information travelling the world over time.

    A rumor starts LOCAL and climbs ``RUMOR_LADDER`` a rung at a time — usually via a
    scheduled ``rumor_spread`` event — so news reaches another district *later*, not
    instantly.
    """

    __tablename__ = "rumors"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    content: Mapped[str] = mapped_column(Text)
    truth: Mapped[bool] = mapped_column(Boolean, default=True)   # rumors may be inaccurate
    origin_location_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    spread_stage: Mapped[int] = mapped_column(Integer, default=0)
    known_scope: Mapped[str] = mapped_column(String(20), default="LOCAL")
    source_event_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
