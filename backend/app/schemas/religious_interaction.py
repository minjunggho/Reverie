"""Typed boundaries for faith-aware NPC and temple interactions."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class TempleArea(str, Enum):
    PUBLIC = "PUBLIC"
    MEMBER = "MEMBER"
    CLERGY_ONLY = "CLERGY_ONLY"
    RESTRICTED_ARCHIVE = "RESTRICTED_ARCHIVE"
    SACRED_INNER_CHAMBER = "SACRED_INNER_CHAMBER"
    EMERGENCY_SANCTUARY = "EMERGENCY_SANCTUARY"


class TempleServiceKind(str, Enum):
    HEALING = "HEALING"
    FUNERAL = "FUNERAL"
    RELIGIOUS_EDUCATION = "RELIGIOUS_EDUCATION"
    RITUAL = "RITUAL"
    DONATION = "DONATION"
    LODGING = "LODGING"
    RELIGIOUS_ITEM_SALE = "RELIGIOUS_ITEM_SALE"


class TempleServicePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: TempleServiceKind
    available: bool = True
    price: dict[str, int] = Field(default_factory=dict)
    required_area: TempleArea = TempleArea.PUBLIC
    requirement: str | None = None


class TemplePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    deity_key: str = Field(min_length=1)
    location_id: str
    faction_id: str | None = None
    public_access: bool = True
    member_access: bool = False
    clergy_access: bool = False
    emergency_sanctuary: bool = False
    current_threat: str | None = None
    services: tuple[TempleServicePolicy, ...] = ()
    provenance: str = Field(min_length=1)


class ReligiousKnowledgeSource(str, Enum):
    VISIBLE_SYMBOL = "VISIBLE_SYMBOL"
    RELIGIOUS_CLOTHING = "RELIGIOUS_CLOTHING"
    PRIOR_CONVERSATION = "PRIOR_CONVERSATION"
    PUBLIC_REPUTATION = "PUBLIC_REPUTATION"
    TEMPLE_RECORD = "TEMPLE_RECORD"
    WITNESSED_RITUAL = "WITNESSED_RITUAL"
    SHARED_FACTION = "SHARED_FACTION"
    PLAYER_DISCLOSURE = "PLAYER_DISCLOSURE"


class DoctrineContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    deity_key: str
    values: tuple[str, ...] = ()
    supported_event_tags: tuple[str, ...] = ()
    opposed_event_tags: tuple[str, ...] = ()


class ReligiousInteractionContext(BaseModel):
    """A compact, epistemically scoped prompt payload; never full pantheon lore."""

    model_config = ConfigDict(frozen=True)

    campaign_id: str
    npc_id: str
    listener_ref: str
    player_public_belief: dict | None = None
    player_known_private_belief: dict | None = None
    visible_symbols: tuple[str, ...] = ()
    knowledge_sources: tuple[ReligiousKnowledgeSource, ...] = ()
    npc_belief: dict | None = None
    npc_religious_role: str | None = None
    npc_religious_knowledge: str | None = None
    religious_faction_id: str | None = None
    shared_deity: bool = False
    allied_deities: tuple[str, ...] = ()
    rival_deities: tuple[str, ...] = ()
    enemy_faiths: tuple[str, ...] = ()
    doctrine: tuple[DoctrineContext, ...] = ()
    known_religious_behavior: tuple[str, ...] = ()
    relationship_state: dict | None = None
    important_religious_memories: tuple[str, ...] = ()
    temple_access_state: dict | None = None
    current_religious_context: tuple[str, ...] = ()

    def as_prompt_block(self) -> str:
        lines = ["RELIGIOUS_CONTEXT (use naturally; belief alone grants no trust or mechanics):"]
        if self.player_public_belief:
            lines.append(f"- player_public_belief={self.player_public_belief}")
        if self.player_known_private_belief:
            lines.append(f"- player_belief_known_to_this_npc={self.player_known_private_belief}")
        if self.visible_symbols:
            lines.append(f"- visible_symbols={list(self.visible_symbols)}")
        if self.npc_belief:
            lines.append(f"- npc_belief={self.npc_belief}")
        if self.npc_religious_role:
            lines.append(
                f"- npc_role={self.npc_religious_role}; knowledge={self.npc_religious_knowledge}"
            )
        if self.shared_deity:
            lines.append("- shared_deity=true; recognition is allowed, automatic trust/help is forbidden")
        if self.allied_deities:
            lines.append(f"- allied_deities={list(self.allied_deities)}")
        if self.rival_deities or self.enemy_faiths:
            lines.append(
                f"- religious_tension=rivals:{list(self.rival_deities)} enemies:{list(self.enemy_faiths)}; "
                "violence is not automatic"
            )
        for item in self.doctrine:
            lines.append(f"- doctrine[{item.deity_key}] values={list(item.values)}")
        if self.important_religious_memories:
            lines.append(f"- religious_memories={list(self.important_religious_memories)}")
        if self.temple_access_state:
            lines.append(f"- temple_access={self.temple_access_state}")
        return "\n".join(lines) if len(lines) > 1 else ""


class ReligiousOutcomeKind(str, Enum):
    RECOGNITION = "RECOGNITION"
    DIALOGUE_STANCE = "DIALOGUE_STANCE"
    REQUEST_PROOF = "REQUEST_PROOF"
    WARNING = "WARNING"
    REFUSAL = "REFUSAL"
    SERVICE_AVAILABILITY = "SERVICE_AVAILABILITY"
    ACCESS_STATE_CHANGE = "ACCESS_STATE_CHANGE"
    MEMORY_CREATION = "MEMORY_CREATION"
    RELATIONSHIP_DELTA = "RELATIONSHIP_DELTA"
    FACTION_REPUTATION_CHANGE = "FACTION_REPUTATION_CHANGE"
    RUMOR_OR_CLUE = "RUMOR_OR_CLUE"
    QUEST_PROPOSAL = "QUEST_PROPOSAL"
    SCHEDULED_CONSEQUENCE = "SCHEDULED_CONSEQUENCE"


class ValidatedReligiousOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ReligiousOutcomeKind
    reason: str
    payload: dict = Field(default_factory=dict)


class TempleAccessDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    area: TempleArea
    reason: str


__all__ = [
    "DoctrineContext", "ReligiousInteractionContext", "ReligiousKnowledgeSource",
    "ReligiousOutcomeKind", "TempleAccessDecision", "TempleArea", "TemplePolicy",
    "TempleServiceKind", "TempleServicePolicy", "ValidatedReligiousOutcome",
]
