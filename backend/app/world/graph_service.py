"""WorldGraphService — the authoritative travel graph + natural exit resolution.

The engine (not narration) owns geography. `resolve_exit` maps a player's textual
movement reference ("ออกไปข้างนอก", "ขึ้นชั้นสอง", a destination name) to a canonical
LocationConnection using conservative matching — never by list order, never invented.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.location import Location
from app.models.world_graph import LocationConnection


def _norm(s: str) -> str:
    return " ".join(unicodedata.normalize("NFC", (s or "")).casefold().split())


# Thai/English hints for common movement references → connection direction/label.
_OUTSIDE = ("ข้างนอก", "ออกไป", "outside", "out", "ประตูหน้า", "front door", "ออกนอก")
_UP = ("ชั้นสอง", "ขึ้นบน", "ขึ้นไป", "upstairs", "up", "ชั้นบน")
_DOWN = ("ชั้นล่าง", "ลงไป", "ลงบันได", "downstairs", "down")
_BACK = ("กลับ", "ย้อนกลับ", "back", "return")


@dataclass
class ExitMatch:
    connection: LocationConnection
    to_name: str
    confidence: float


class WorldGraphService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_connection(
        self, *, campaign_id: str, from_location_id: str, to_location_id: str,
        label: str = "", direction: str = "", travel_minutes: int = 0,
        obvious: bool = True, one_way: bool = False, access_state: str = "open",
        bidirectional_label: str | None = None,
        provenance: str = "IMPORTED_EXPLICIT", traversal_mode: str = "walk",
    ) -> LocationConnection:
        conn = LocationConnection(
            campaign_id=campaign_id, from_location_id=from_location_id,
            to_location_id=to_location_id, label=label, direction=direction,
            travel_minutes=travel_minutes, obvious=obvious, one_way=one_way,
            access_state=access_state, provenance=provenance, traversal_mode=traversal_mode,
        )
        self.session.add(conn)
        await self.session.flush()
        if not one_way:
            back = LocationConnection(
                campaign_id=campaign_id, from_location_id=to_location_id,
                to_location_id=from_location_id,
                label=bidirectional_label or "กลับ", direction=_reverse(direction),
                travel_minutes=travel_minutes, obvious=obvious, access_state=access_state,
                provenance=provenance, traversal_mode=traversal_mode,
            )
            self.session.add(back)
            await self.session.flush()
        return conn

    async def exits(self, from_location_id: str) -> list[LocationConnection]:
        return list((await self.session.execute(
            select(LocationConnection).where(
                LocationConnection.from_location_id == from_location_id)
        )).scalars())

    async def resolve_exit(self, *, from_location_id: str, reference: str,
                           obvious_only: bool = True) -> ExitMatch | None:
        """Best canonical exit for a movement reference, or None if unresolvable."""
        exits = await self.exits(from_location_id)
        if obvious_only:
            candidates = [e for e in exits if e.obvious] or exits
        else:
            candidates = exits
        if not candidates:
            return None
        ref = _norm(reference)

        # 1. exact destination NAME match.
        for e in candidates:
            dest = await self.session.get(Location, e.to_location_id)
            if dest and (_norm(dest.name) in ref or ref in _norm(dest.name)) and _norm(dest.name):
                return ExitMatch(e, dest.name, 0.95)
        # 2. exact exit LABEL match.
        for e in candidates:
            if e.label and _norm(e.label) in ref:
                dest = await self.session.get(Location, e.to_location_id)
                return ExitMatch(e, dest.name if dest else "", 0.9)
        # 3. direction keyword families.
        family = None
        if any(w in ref for w in _OUTSIDE):
            family = "outside"
        elif any(w in ref for w in _UP):
            family = "up"
        elif any(w in ref for w in _DOWN):
            family = "down"
        elif any(w in ref for w in _BACK):
            family = "back"
        if family:
            for e in candidates:
                # An EMPTY direction never means "outside" (§4): only an edge
                # explicitly tagged with the direction family matches.
                if _norm(e.direction) == family:
                    dest = await self.session.get(Location, e.to_location_id)
                    return ExitMatch(e, dest.name if dest else "", 0.8)
        # 4. single obvious exit + a generic "go/leave" reference → that exit.
        if len(candidates) == 1 and any(w in ref for w in ("ไป", "เดิน", "go", "walk", "leave", "ออก")):
            dest = await self.session.get(Location, candidates[0].to_location_id)
            return ExitMatch(candidates[0], dest.name if dest else "", 0.7)
        return None

    async def get_location(self, location_id: str) -> Location:
        loc = await self.session.get(Location, location_id)
        if loc is None:
            raise NotFoundError(f"location {location_id} not found")
        return loc


def _reverse(direction: str) -> str:
    return {"up": "down", "down": "up", "outside": "inside",
            "inside": "outside", "uphill": "downhill", "downhill": "uphill"}.get(direction, "back")
