"""RouteService — multi-hop navigation over the authoritative travel graph.

`WorldGraphService.resolve_exit` answers "which single adjacent edge does this
movement reference mean?". That is not enough: a player at the tavern who says
"ไปมหาวิหาร" (go to the cathedral) names a destination several hops away with no
direct edge. The engine must find the destination anywhere in the reachable world,
compute the shortest legal route to it, and traverse the WHOLE route so elapsed time
and world state reflect the journey — never teleporting.

The outside rule (§4): you cannot pass directly from one building to an unrelated
building. Because the graph is authoritative, a correct graph already routes
tavern → street → … → shop through the exterior. Where a building has no way OUT to
its exterior (a sparse import), `infer_exterior_link` commits the minimum connective
edge deterministically (no LLM) so the world is always explorable — and never routes
THROUGH an unrelated building.

Nothing here invents destinations. If no authored/reachable location matches, the
caller falls back to bounded expansion (ordinary places) or a focused clarification —
never a fabricated place chosen "because it was the only edge".
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.location import Location
from app.models.world_graph import LocationConnection

# Location types that are "inside" — the outside rule governs movement between these.
_INTERIOR_TYPES = frozenset({"LOCATION", "BUILDING", "ROOM"})


class DestinationClass(Enum):
    """How a movement reference resolves against the world (§5 classification)."""

    EXISTING_ADJACENT = "existing_adjacent"        # a direct authored exit
    EXISTING_ROUTED = "existing_routed"            # authored, reachable via a multi-hop route
    ORDINARY_EXPANDABLE = "ordinary_expandable"    # unauthored ordinary place → safe expansion
    AMBIGUOUS = "ambiguous"                        # intent unclear → one focused question
    UNREACHABLE = "unreachable"                    # named but no legal route (blocked/locked)


@dataclass
class RoutePlan:
    """An ordered, legal path through the graph. `hops[i]` goes from
    `hops[i-1]`'s destination to `hops[i]`'s destination; the first hop starts at the
    origin. Detail may be compressed in narration, but time/state reflect every hop."""

    hops: list[LocationConnection]
    destination_id: str
    destination_name: str = ""
    waypoint_names: list[str] = field(default_factory=list)   # intermediate location names, in order

    @property
    def total_minutes(self) -> int:
        return sum(h.travel_minutes for h in self.hops)

    @property
    def is_multi_hop(self) -> bool:
        return len(self.hops) > 1


@dataclass
class DestinationResolution:
    klass: DestinationClass
    target: Location | None = None
    route: RoutePlan | None = None


class RouteService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- pathfinding ------------------------------------------------------------

    async def _open_edges(self, campaign_id: str) -> dict[str, list[LocationConnection]]:
        """Adjacency of PASSABLE edges (access_state == open) for the campaign."""
        rows = (await self.session.execute(select(LocationConnection).where(
            LocationConnection.campaign_id == campaign_id))).scalars()
        adj: dict[str, list[LocationConnection]] = {}
        for e in rows:
            if e.access_state == "open":
                adj.setdefault(e.from_location_id, []).append(e)
        return adj

    async def find_route(
        self, *, campaign_id: str, from_location_id: str, to_location_id: str,
        obvious_only: bool = True,
    ) -> RoutePlan | None:
        """Shortest legal route (by total travel_minutes) between two locations, or
        None if there is no path over open — and, when requested, obvious — edges.
        Ties break toward fewer hops so a direct authored edge always wins."""
        if from_location_id == to_location_id:
            return RoutePlan(hops=[], destination_id=to_location_id)
        adj = await self._open_edges(campaign_id)
        # Dijkstra keyed by (minutes, hop_count); prev maps node -> edge used to reach it.
        best: dict[str, tuple[int, int]] = {from_location_id: (0, 0)}
        prev: dict[str, LocationConnection] = {}
        pq: list[tuple[int, int, str]] = [(0, 0, from_location_id)]
        while pq:
            mins, hops, node = heapq.heappop(pq)
            if (mins, hops) > best.get(node, (mins, hops)):
                continue
            if node == to_location_id:
                break
            for e in adj.get(node, ()):
                if obvious_only and not e.obvious:
                    continue
                cand = (mins + e.travel_minutes, hops + 1)
                if cand < best.get(e.to_location_id, (10**9, 10**9)):
                    best[e.to_location_id] = cand
                    prev[e.to_location_id] = e
                    heapq.heappush(pq, (cand[0], cand[1], e.to_location_id))
        if to_location_id not in prev and to_location_id != from_location_id:
            return None
        # Rebuild the ordered hop list.
        hops: list[LocationConnection] = []
        cur = to_location_id
        while cur != from_location_id:
            e = prev[cur]
            hops.append(e)
            cur = e.from_location_id
        hops.reverse()
        waypoints: list[str] = []
        for e in hops[:-1]:
            dest = await self.session.get(Location, e.to_location_id)
            if dest is not None:
                waypoints.append(dest.name)
        target = await self.session.get(Location, to_location_id)
        return RoutePlan(hops=hops, destination_id=to_location_id,
                         destination_name=target.name if target else "",
                         waypoint_names=waypoints)

    # --- destination resolution / classification --------------------------------

    async def resolve_destination(
        self, *, campaign_id: str, from_location_id: str, reference: str,
    ) -> DestinationResolution:
        """Resolve a movement reference against the WHOLE reachable world, not just
        adjacent edges. Multilingual + alias + NPC-directed resolution is delegated to
        the one authoritative `LocationResolver`; a named authored destination outranks
        a generic verb; a place that exists but has no open route is UNREACHABLE (a
        locked gate, not a lie); two equally-good matches ask instead of guessing."""
        from app.world.location_resolver import LocationResolver

        result = await LocationResolver(self.session).resolve(
            campaign_id=campaign_id, reference=reference, exclude_id=from_location_id)

        if result.is_ambiguous or result.npc_location_unknown:
            # Several places answer to the name, or the named NPC's whereabouts are
            # unknown — the caller asks one focused question, never a coin-flip.
            return DestinationResolution(DestinationClass.AMBIGUOUS)
        if not result.resolved:
            # No authored/known place by that name → the caller decides expansion vs.
            # a focused question. We only report the class, never invent a location.
            return DestinationResolution(DestinationClass.ORDINARY_EXPANDABLE)

        target = result.match.location
        route = await self.find_route(
            campaign_id=campaign_id, from_location_id=from_location_id,
            to_location_id=target.id)
        if route is None:
            return DestinationResolution(DestinationClass.UNREACHABLE, target=target)
        klass = (DestinationClass.EXISTING_ADJACENT if not route.is_multi_hop
                 else DestinationClass.EXISTING_ROUTED)
        return DestinationResolution(klass, target=target, route=route)

    # --- the outside rule -------------------------------------------------------

    @staticmethod
    def _is_interior(loc: Location | None) -> bool:
        return loc is not None and (loc.location_type or "LOCATION").upper() in _INTERIOR_TYPES

    async def route_obeys_outside_rule(self, route: RoutePlan) -> bool:
        """True unless the route steps DIRECTLY from one interior location into a
        different, unrelated interior location — i.e. teleporting building→building
        without any exterior in between. Moving between interiors that share a parent
        (rooms of one building) is fine; leaving to an exterior is fine."""
        for hop in route.hops:
            src = await self.session.get(Location, hop.from_location_id)
            dst = await self.session.get(Location, hop.to_location_id)
            if self._is_interior(src) and self._is_interior(dst):
                # Rooms of ONE building may connect directly: one is the other's
                # parent, or they share an INTERIOR parent (the building itself). Two
                # separate buildings that merely share a DISTRICT do not — you must
                # step out into the exterior between them.
                if dst.parent_id == src.id or src.parent_id == dst.id:
                    continue
                if src.parent_id and src.parent_id == dst.parent_id:
                    shared = await self.session.get(Location, src.parent_id)
                    if self._is_interior(shared):
                        continue
                return False
        return True

    async def infer_exterior_link(
        self, *, campaign_id: str, location_id: str,
    ) -> LocationConnection | None:
        """Guarantee an interior location has a way OUT to its exterior (its parent),
        so the world is always explorable and routes never pass through an unrelated
        building. Deterministic (no LLM); commits + persists the minimal edge; a
        no-op when a link already exists or there is no parent to leave into."""
        loc = await self.session.get(Location, location_id)
        if loc is None or not loc.parent_id:
            return None
        parent = await self.session.get(Location, loc.parent_id)
        if parent is None:
            return None
        existing = (await self.session.execute(select(LocationConnection).where(
            LocationConnection.campaign_id == campaign_id,
            LocationConnection.from_location_id == location_id,
            LocationConnection.to_location_id == loc.parent_id))).scalars().first()
        if existing is not None:
            return existing
        # Reuse the graph service so the reverse (enter-from-outside) edge is created
        # too, keeping the world bidirectionally traversable.
        from app.world.graph_service import WorldGraphService

        return await WorldGraphService(self.session).add_connection(
            campaign_id=campaign_id, from_location_id=location_id,
            to_location_id=loc.parent_id, label="ออกไปข้างนอก", direction="outside",
            travel_minutes=0, obvious=True, provenance="AI_INFERRED_CONNECTOR",
            bidirectional_label=f"เข้า{loc.name}")
