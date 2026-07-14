"""NPC subsystem: NPC state, epistemic records (knowledge/belief/suspicion), and
basic social interaction."""
from app.npcs.knowledge_service import NPCKnowledgeService
from app.npcs.belief_generator import (
    NPCBeliefContext,
    NPCBeliefGenerator,
    NPCBeliefProposal,
    knowledge_for_role,
)
from app.npcs.npc_service import NPCService
from app.npcs.social_service import NPCSocialService, SocialResult

__all__ = [
    "NPCBeliefContext", "NPCBeliefGenerator", "NPCBeliefProposal",
    "NPCService", "NPCKnowledgeService", "NPCSocialService", "SocialResult",
    "knowledge_for_role",
]
