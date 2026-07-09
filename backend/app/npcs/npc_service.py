"""NPC CRUD + attitude access. Epistemic records (knowledge/belief) land in Phase 11."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.npc import NPC


class NPCService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_npc(
        self,
        *,
        campaign_id: str,
        name: str,
        personality: str = "",
        voice_register: str = "",
        goals: list[str] | None = None,
        current_location_id: str | None = None,
        emotional_state: str = "calm",
    ) -> NPC:
        npc = NPC(
            campaign_id=campaign_id,
            name=name,
            personality=personality,
            voice_register=voice_register,
            goals=goals or [],
            current_location_id=current_location_id,
            emotional_state=emotional_state,
        )
        self.session.add(npc)
        await self.session.flush()
        return npc

    async def get_npc(self, npc_id: str) -> NPC:
        npc = await self.session.get(NPC, npc_id)
        if npc is None:
            raise NotFoundError(f"npc {npc_id} not found")
        return npc

    async def set_attitude(self, npc: NPC, entity_ref: str, attitude: str) -> NPC:
        attitudes = dict(npc.attitudes or {})
        attitudes[entity_ref] = attitude
        npc.attitudes = attitudes  # reassign so JSON change is tracked
        return npc
