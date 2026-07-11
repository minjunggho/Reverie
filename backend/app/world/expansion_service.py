"""WorldExpansionService — canon-consistent AI world expansion.

The owner can't author every shop and alley. When a player seeks an unauthored
ordinary place, the AI proposes a `LocationDraft` from BOUNDED settlement context;
the engine validates it, commits it (provenance AI_EXPANDED) with a connection from
the current location and an optional proprietor NPC, and only THEN is it narrated.

Product law: AI may expand the world; AI may not repeatedly rewrite it. A committed
location keeps its identity/name/position/connections/state unless canonical events
change them. `find_or_expand` returns an already-committed location if a matching one
exists, so the same request never regenerates a second copy.
"""
from __future__ import annotations

import unicodedata

from sqlalchemy import select

from app.ai.llm.base import LLMMessage, LLMProvider
from app.ai.prompts.system_prompts import WORLD_EXPANSION_SYSTEM
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.models.location import Location
from app.models.npc import NPC
from app.schemas.llm_io import LocationDraft
from app.world.graph_service import WorldGraphService

log = get_logger(__name__)


def _norm(s: str) -> str:
    return " ".join(unicodedata.normalize("NFC", (s or "")).casefold().split())


class WorldExpansionService:
    def __init__(self, db, provider: LLMProvider) -> None:
        self.db = db
        self.provider = provider

    async def find_or_expand(
        self, *, campaign_id: str, from_location_id: str, request: str,
    ) -> Location | None:
        """Return a canonical location matching `request` reachable from the current
        one — an existing neighbour if present, else a freshly committed AI_EXPANDED
        place. None if the AI can't justify one."""
        # 1. already-committed neighbour that matches? (never regenerate.)
        async with self.db.session() as read:
            graph = WorldGraphService(read)
            for e in await graph.exits(from_location_id):
                dest = await read.get(Location, e.to_location_id)
                if dest and _looks_like(request, dest.name):
                    return dest
            here = await read.get(Location, from_location_id)
            settlement = await self._settlement(read, here)

        # 2. propose a canon-consistent draft.
        draft = await self._propose(request, here, settlement)
        if draft is None:
            return None

        # 3. commit atomically (location + connection + optional NPC), then persist.
        async with self.db.unit_of_work() as s:
            existing = (await s.execute(select(Location).where(
                Location.campaign_id == campaign_id, Location.name == draft.name))).scalars().first()
            if existing is not None:
                return existing
            loc = Location(
                campaign_id=campaign_id, name=draft.name,
                location_type=draft.location_type.upper()[:20] or "LOCATION",
                description_obvious=draft.obvious, provenance="AI_EXPANDED",
                parent_id=here.parent_id if here is not None else None,
                state={"canon_justification": draft.canon_justification,
                       "expanded_from": from_location_id})
            s.add(loc)
            await s.flush()
            await WorldGraphService(s).add_connection(
                campaign_id=campaign_id, from_location_id=from_location_id,
                to_location_id=loc.id, label=draft.connection_label or "ทางเข้า",
                direction="", travel_minutes=draft.travel_minutes, obvious=True)
            if draft.npc_name:
                s.add(NPC(campaign_id=campaign_id, name=draft.npc_name,
                          current_location_id=loc.id))
            loc_id = loc.id
        async with self.db.session() as read:
            return await read.get(Location, loc_id)

    async def _settlement(self, session, loc: Location | None) -> Location | None:
        cur, guard = loc, 0
        while cur is not None and cur.parent_id and guard < 6:
            parent = await session.get(Location, cur.parent_id)
            if parent is not None and parent.location_type in ("SETTLEMENT", "DISTRICT", "REGION"):
                return parent
            cur, guard = parent, guard + 1
        return loc

    async def _propose(self, request: str, here: Location | None,
                       settlement: Location | None) -> LocationDraft | None:
        ctx = (
            f"REQUEST: {request}\n"
            f"CURRENT_LOCATION: {here.name if here else '-'}\n"
            f"SETTLEMENT: {settlement.name if settlement else '-'}\n"
            f"SETTLEMENT_DESC: {settlement.description_obvious if settlement else '-'}"
        )
        messages: list[LLMMessage] = [
            {"role": "system", "content": WORLD_EXPANSION_SYSTEM},
            {"role": "user", "content": ctx},
        ]
        try:
            return await self.provider.generate_location_expansion(messages)
        except LLMError as exc:
            log.warning("world expansion declined: %s", exc)
            return None


def _looks_like(request: str, name: str) -> bool:
    r, n = _norm(request), _norm(name)
    return bool(n) and (n in r or r in n)
