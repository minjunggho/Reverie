"""Character v2 — the full mechanical representation. ALL values engine-authoritative.

Derived values (skill bonuses, save DC, passives) are NOT stored here — the
derivation engine computes them from scores + grants and can explain them.
Companion rows: CharacterGrant / CharacterSpell / ResourceState / ActiveEffect
(see models/progression.py) carry provenance, spells, resources, and effects.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column


class Character(Base, TimestampMixin):
    __tablename__ = "characters"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    owner_member_id: Mapped[str] = fk_id("campaign_members.id")

    name: Mapped[str] = mapped_column(String(120))
    species: Mapped[str] = mapped_column(String(60), default="human")
    char_class: Mapped[str] = mapped_column(String(60), default="fighter")
    # A NARRATIVE preference chosen early (creation) — never mechanical on its own.
    planned_subclass: Mapped[str | None] = mapped_column(String(80), nullable=True, default=None)
    # The ACTIVE subclass — set only when the character reaches its class's
    # subclass_level and the player confirms. This is what grants subclass features.
    active_subclass: Mapped[str | None] = mapped_column(String(80), nullable=True, default=None)
    background: Mapped[str] = mapped_column(String(60), default="")
    ruleset_id: Mapped[str] = mapped_column(String(16), default="srd521")

    # Ability scores (3..20 in the supported subset).
    str_score: Mapped[int] = mapped_column("str_score", Integer, default=10)
    dex_score: Mapped[int] = mapped_column("dex_score", Integer, default=10)
    con_score: Mapped[int] = mapped_column("con_score", Integer, default=10)
    int_score: Mapped[int] = mapped_column("int_score", Integer, default=10)
    wis_score: Mapped[int] = mapped_column("wis_score", Integer, default=10)
    cha_score: Mapped[int] = mapped_column("cha_score", Integer, default=10)

    # Proficiencies. proficiency_bonus is kept for back-compat but derived truth
    # is proficiency_bonus_for_level(level).
    proficiencies: Mapped[list[str]] = mapped_column(JSON, default=list)      # skills
    expertise: Mapped[list[str]] = mapped_column(JSON, default=list)          # skills
    save_proficiencies: Mapped[list[str]] = mapped_column(JSON, default=list)  # abilities
    tool_proficiencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    languages: Mapped[list[str]] = mapped_column(JSON, default=list)
    proficiency_bonus: Mapped[int] = mapped_column(Integer, default=2)

    # Combat block.
    hp: Mapped[int] = mapped_column(Integer, default=10)
    max_hp: Mapped[int] = mapped_column(Integer, default=10)
    temp_hp: Mapped[int] = mapped_column(Integer, default=0)
    ac: Mapped[int] = mapped_column(Integer, default=10)
    speed: Mapped[int] = mapped_column(Integer, default=30)
    hit_die: Mapped[int] = mapped_column(Integer, default=8)          # die size
    hit_dice_remaining: Mapped[int] = mapped_column(Integer, default=1)

    # Dying state (SRD 5.2.1 death saves).
    death_saves: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=lambda: {"successes": 0, "failures": 0}
    )
    stable: Mapped[bool] = mapped_column(Boolean, default=False)
    dead: Mapped[bool] = mapped_column(Boolean, default=False)

    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    exhaustion: Mapped[int] = mapped_column(Integer, default=0)

    conditions: Mapped[list[str]] = mapped_column(JSON, default=list)
    resources: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # legacy misc

    # Canonical physical position (where this character IS). Scene presence derives
    # from co-location; supports party splits. NULL until placed at session start.
    location_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    # Explicit, persistent travel CONSENT (§18). When set to another character's id,
    # this character has agreed to travel with that leader and moves when the leader
    # moves (and only while co-located). Co-location alone is NEVER consent — a
    # character with no follow state stays put when someone else leaves. Cleared when
    # this character acts/moves on their own initiative or explicitly stops following.
    following_character_id: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)

    # Narrative hooks (experience overhaul) — the DM engine's raw material.
    hooks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    appearance: Mapped[str] = mapped_column(Text, default="")

    # The COMPLETE player-authored creation text, verbatim, never summarized away.
    # Everything the player wrote during creation is preserved here so the DM can
    # always return to the source, not just the extracted structure.
    origin_text: Mapped[str] = mapped_column(Text, default="")
    # Structured character identity extracted from origin_text: pronouns, ancestry,
    # appearance breakdown, culture, family/mentors/rivals, goals/fears/ideals/bonds/
    # flaws/secrets, narrative class/ancestry (which may exceed the mechanical
    # chassis), and reviewable evolution `seeds`. The extraction NEVER replaces
    # origin_text; both are kept. See app/services/campaigns/identity.py.
    identity: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Explicit alternate names for entity resolution (e.g. Thai transliteration
    # "อาเรีย" for "Aria"). The player's Discord display name is NOT an alias.
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)

    def ability_score(self, ability: str) -> int:
        return int(getattr(self, f"{ability.lower()}_score"))

    @property
    def dying(self) -> bool:
        return self.hp <= 0 and not self.dead and not self.stable
