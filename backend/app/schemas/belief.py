"""Shared, persistence-safe belief profile for player characters and NPCs."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BeliefStance(str, Enum):
    DEVOUT = "DEVOUT"
    BELIEVER = "BELIEVER"
    CULTURAL = "CULTURAL"
    QUESTIONING = "QUESTIONING"
    DOUBTFUL = "DOUBTFUL"
    FORMER_BELIEVER = "FORMER_BELIEVER"
    AGNOSTIC = "AGNOSTIC"
    ATHEIST = "ATHEIST"
    HOSTILE_TO_RELIGION = "HOSTILE_TO_RELIGION"
    SECRET_BELIEVER = "SECRET_BELIEVER"
    MULTI_FAITH = "MULTI_FAITH"


class DevotionLevel(str, Enum):
    NONE = "NONE"
    CASUAL = "CASUAL"
    ORDINARY = "ORDINARY"
    COMMITTED = "COMMITTED"
    DEVOUT = "DEVOUT"


class BeliefVisibility(str, Enum):
    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"
    SECRET = "SECRET"


class ReligiousRole(str, Enum):
    PRIEST = "PRIEST"
    ACOLYTE = "ACOLYTE"
    TEMPLE_GUARD = "TEMPLE_GUARD"
    PILGRIM = "PILGRIM"
    MONK = "MONK"
    THEOLOGIAN = "THEOLOGIAN"
    HERETIC = "HERETIC"
    CULTIST = "CULTIST"
    INQUISITOR = "INQUISITOR"
    SHRINE_KEEPER = "SHRINE_KEEPER"
    FUNERAL_KEEPER = "FUNERAL_KEEPER"
    RELIGIOUS_OFFICIAL = "RELIGIOUS_OFFICIAL"
    ORDINARY_FOLLOWER = "ORDINARY_FOLLOWER"


class ReligiousKnowledgeLevel(str, Enum):
    NONE = "NONE"
    CULTURAL = "CULTURAL"
    INFORMED = "INFORMED"
    DEEP = "DEEP"
    SPECIALIST = "SPECIALIST"


class BeliefSource(str, Enum):
    PLAYER_AUTHORED = "PLAYER_AUTHORED"
    OWNER_EDITED = "OWNER_EDITED"
    IMPORTED_CANON = "IMPORTED_CANON"
    AI_GENERATED = "AI_GENERATED"


class BeliefProfile(BaseModel):
    """A person's religious identity, independent from class mechanics."""

    model_config = ConfigDict(frozen=True, use_enum_values=False)

    primary_deity_key: str | None = None
    secondary_deity_keys: tuple[str, ...] = ()
    stance: BeliefStance
    devotion: DevotionLevel = DevotionLevel.NONE
    visibility: BeliefVisibility = BeliefVisibility.PUBLIC
    religious_role: ReligiousRole | None = None
    knowledge_level: ReligiousKnowledgeLevel = ReligiousKnowledgeLevel.NONE
    temple_or_faction_id: str | None = None
    personal_reason: str | None = None
    personal_interpretation: str | None = None
    sacred_symbol: str | None = None
    practices: tuple[str, ...] = ()
    taboo: str | None = None
    doubt: str | None = None
    religious_conflict: str | None = None
    former_deity_key: str | None = None
    conversion_history: tuple[str, ...] = ()
    owner_notes: str | None = None
    source: BeliefSource
    provenance: str = Field(min_length=1)

    @field_validator(
        "primary_deity_key", "temple_or_faction_id", "personal_reason",
        "personal_interpretation", "sacred_symbol", "taboo", "doubt",
        "religious_conflict", "former_deity_key", "owner_notes",
        mode="before",
    )
    @classmethod
    def _clean_optional_text(cls, value):
        if value is None:
            return None
        clean = str(value).strip()
        return clean or None

    @field_validator(
        "secondary_deity_keys", "practices", "conversion_history", mode="before"
    )
    @classmethod
    def _clean_lists(cls, value):
        return tuple(str(item).strip() for item in (value or ()) if str(item).strip())

    @model_validator(mode="after")
    def _coherent_profile(self) -> "BeliefProfile":
        if len(self.secondary_deity_keys) != len(set(self.secondary_deity_keys)):
            raise ValueError("secondary_deity_keys must be unique")
        if self.primary_deity_key in self.secondary_deity_keys:
            raise ValueError("primary deity cannot also be a secondary deity")
        if self.stance in {
            BeliefStance.AGNOSTIC,
            BeliefStance.ATHEIST,
            BeliefStance.HOSTILE_TO_RELIGION,
            BeliefStance.FORMER_BELIEVER,
        } and self.primary_deity_key is not None:
            raise ValueError(f"stance {self.stance.value} cannot have a primary deity")
        if self.stance is BeliefStance.SECRET_BELIEVER and self.visibility is not BeliefVisibility.SECRET:
            raise ValueError("SECRET_BELIEVER must use SECRET visibility")
        if self.stance is BeliefStance.MULTI_FAITH and not (
            self.primary_deity_key or self.secondary_deity_keys
        ):
            raise ValueError("MULTI_FAITH requires at least one deity")
        return self


__all__ = [
    "BeliefProfile",
    "BeliefSource",
    "BeliefStance",
    "BeliefVisibility",
    "DevotionLevel",
    "ReligiousKnowledgeLevel",
    "ReligiousRole",
]
