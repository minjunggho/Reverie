"""SQLAlchemy ORM models — the canonical state of record.

Importing this package registers every model on `Base.metadata` so
`Database.create_all()` (tests) and Alembic (production) see the full schema.

Present (Phases 0–9 + vertical slice):
    User, Campaign, CampaignMember, Character, Session, Scene, Event, Location,
    NPC, ProcessedMessage, KnowledgeRecord, Secret.

Scaffolded in later phases (documented in PROGRESS.md, not yet imported here):
    NPC epistemic tables (Phase 11), Threat/ScheduledWorldEvent (Phase 12),
    CombatEncounter/Combatant (Phase 13), ItemDefinition/InventoryEntry, Quest.
"""
from app.models.campaign import Campaign, CampaignMember
from app.models.campaign_progression import Chapter, Clue
from app.models.canon_import import CanonImport
from app.models.character import Character
from app.models.character_draft import CharacterDraft
from app.models.consequences import (
    CrimeRecord,
    Faction,
    Quest,
    Reputation,
    Rumor,
)
from app.models.combat import Combatant, CombatEncounter
from app.models.economy import CurrencyTransaction, Wallet
from app.models.event import Event
from app.models.items import InventoryEntry, ItemDefinition
from app.models.knowledge import KnowledgeRecord, Secret
from app.models.location import Location
from app.models.npc import NPC
from app.models.npc_epistemic import NPCFact, NPCIntention, NPCMemory, NPCRelationship
from app.models.processed_message import ProcessedMessage
from app.models.progression import (
    ActiveEffect,
    CharacterGrant,
    CharacterSpell,
    ResourceState,
)
from app.models.scene import Scene
from app.models.session import Session
from app.models.user import User
from app.models.decision_window import ActionSubmission, DecisionWindow
from app.models.world import ScheduledWorldEvent, Threat
from app.models.world_graph import CampaignCanonRecord, LocationConnection

__all__ = [
    "DecisionWindow",
    "ActionSubmission",
    "User",
    "Campaign",
    "CampaignMember",
    "CanonImport",
    "Character",
    "Session",
    "Scene",
    "Event",
    "Location",
    "NPC",
    "NPCFact",
    "NPCRelationship",
    "NPCMemory",
    "NPCIntention",
    "ProcessedMessage",
    "KnowledgeRecord",
    "Secret",
    "Threat",
    "ScheduledWorldEvent",
    "CombatEncounter",
    "Combatant",
    "CharacterDraft",
    "ItemDefinition",
    "InventoryEntry",
    "CharacterGrant",
    "CharacterSpell",
    "ResourceState",
    "ActiveEffect",
    "LocationConnection",
    "CampaignCanonRecord",
    "Wallet",
    "CurrencyTransaction",
    "Faction",
    "Reputation",
    "CrimeRecord",
    "Quest",
    "Rumor",
    "Chapter",
    "Clue",
]
