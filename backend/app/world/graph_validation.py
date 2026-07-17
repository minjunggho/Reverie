"""World-graph validation over COMMITTED geography (§11).

`campaign_validation.validate_campaign` checks a `CampaignProposal` before commit;
this checks the live `Location` + `LocationConnection` rows after commit — the graph
players actually walk. Every issue is classified so the owner (and the engine) know
what to do with it:

- BLOCKING_ERROR       — structurally impossible; the world is not shippable.
- OWNER_REVIEW_REQUIRED — a real problem only the owner should resolve.
- SAFE_AUTO_REPAIR     — an ordinary missing connector the engine may add itself.
- WARNING              — surfaced, not fatal.

`safe_auto_repair` applies ONLY the SAFE_AUTO_REPAIR class (missing exterior exits),
reusing the deterministic `RouteService.infer_exterior_link`. It never invents canon.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.location import Location
from app.models.world_graph import LocationConnection

_INTERIOR_TYPES = frozenset({"LOCATION", "BUILDING", "ROOM"})

BLOCKING = "BLOCKING_ERROR"
OWNER_REVIEW = "OWNER_REVIEW_REQUIRED"
AUTO_REPAIR = "SAFE_AUTO_REPAIR"
WARNING = "WARNING"


@dataclass
class GraphIssue:
    kind: str
    category: str
    message: str
    refs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "category": self.category,
                "message": self.message, "refs": list(self.refs)}


@dataclass
class GraphReport:
    issues: list[GraphIssue] = field(default_factory=list)

    def add(self, kind, category, message, refs=None) -> None:
        self.issues.append(GraphIssue(kind, category, message, list(refs or [])))

    def _of(self, category) -> list[GraphIssue]:
        return [i for i in self.issues if i.category == category]

    @property
    def blocking(self) -> list[GraphIssue]:
        return self._of(BLOCKING)

    @property
    def owner_review(self) -> list[GraphIssue]:
        return self._of(OWNER_REVIEW)

    @property
    def auto_repairable(self) -> list[GraphIssue]:
        return self._of(AUTO_REPAIR)

    @property
    def warnings(self) -> list[GraphIssue]:
        return self._of(WARNING)

    @property
    def ok(self) -> bool:
        return not self.blocking


def _interior(loc: Location | None) -> bool:
    return loc is not None and (loc.location_type or "LOCATION").upper() in _INTERIOR_TYPES


async def validate_world_graph(session: AsyncSession, campaign_id: str) -> GraphReport:
    report = GraphReport()
    locs = list((await session.execute(select(Location).where(
        Location.campaign_id == campaign_id))).scalars())
    by_id = {l.id: l for l in locs}
    conns = list((await session.execute(select(LocationConnection).where(
        LocationConnection.campaign_id == campaign_id))).scalars())

    # --- parent hierarchy: missing parent, cycle -------------------------------
    for loc in locs:
        if loc.parent_id and loc.parent_id not in by_id:
            report.add("missing_parent", BLOCKING,
                       f"'{loc.name}' has parent '{loc.parent_id}' that does not exist",
                       [loc.id])
        # cycle: walk the parent chain; a repeat means a loop.
        seen, cur, guard = set(), loc, 0
        while cur is not None and cur.parent_id and guard < 64:
            if cur.parent_id in seen:
                report.add("parent_cycle", BLOCKING,
                           f"'{loc.name}' is in a parent cycle", [loc.id])
                break
            seen.add(cur.parent_id)
            cur = by_id.get(cur.parent_id)
            guard += 1

    # --- edges: refs, cross-campaign, time, duplicates, teleports --------------
    seen_pairs: set[tuple[str, str]] = set()
    outbound_open: dict[str, int] = {l.id: 0 for l in locs}
    inbound_open: dict[str, int] = {l.id: 0 for l in locs}
    for e in conns:
        src, dst = by_id.get(e.from_location_id), by_id.get(e.to_location_id)
        if src is None or dst is None:
            # A dangling ref, or an edge that reaches into another campaign's world.
            other = e.from_location_id if src is None else e.to_location_id
            other_row = await session.get(Location, other)
            if other_row is not None and other_row.campaign_id != campaign_id:
                report.add("cross_campaign_edge", BLOCKING,
                           f"connection {e.id} crosses into another campaign", [e.id])
            else:
                report.add("broken_edge_ref", BLOCKING,
                           f"connection {e.id} references a missing location", [e.id])
            continue
        if e.travel_minutes < 0:
            report.add("negative_travel_time", BLOCKING,
                       f"connection {src.name}→{dst.name} has negative travel time", [e.id])
        pair = (e.from_location_id, e.to_location_id)
        if pair in seen_pairs:
            report.add("duplicate_edge", WARNING,
                       f"duplicate connection {src.name}→{dst.name}", [e.id])
        seen_pairs.add(pair)
        if e.access_state == "open":
            outbound_open[e.from_location_id] = outbound_open.get(e.from_location_id, 0) + 1
            inbound_open[e.to_location_id] = inbound_open.get(e.to_location_id, 0) + 1
            # interior→unrelated-interior teleport (the outside rule, §4).
            if _interior(src) and _interior(dst):
                related = (dst.parent_id == src.id or src.parent_id == dst.id
                           or (src.parent_id and src.parent_id == dst.parent_id
                               and _interior(by_id.get(src.parent_id))))
                if not related:
                    report.add("interior_teleport", OWNER_REVIEW,
                               f"'{src.name}' connects DIRECTLY to unrelated interior "
                               f"'{dst.name}' — players should step outside between them",
                               [e.id])

    # --- traps + missing exits -------------------------------------------------
    for loc in locs:
        out, inb = outbound_open.get(loc.id, 0), inbound_open.get(loc.id, 0)
        if out == 0:
            if loc.parent_id and loc.parent_id in by_id:
                # An interior with no way out but a parent to leave into: the engine
                # can add the exterior link deterministically.
                report.add("missing_exit", AUTO_REPAIR,
                           f"'{loc.name}' has no way out — an exterior link can be inferred",
                           [loc.id])
            elif inb > 0:
                # Reachable, but a dead end you can never leave: a one-way trap.
                report.add("one_way_trap", OWNER_REVIEW,
                           f"'{loc.name}' can be entered but never left", [loc.id])

    return report


@dataclass
class ReachabilityReport:
    """Which places the party can actually walk to from the start, and which are
    stranded. The heart of "90 locations, players stuck in 2": a location the graph
    cannot reach is a location that does not exist during play."""

    start_id: str | None
    reachable: set[str] = field(default_factory=set)
    unreachable: list[str] = field(default_factory=list)   # location ids
    orphans: list[str] = field(default_factory=list)       # unreachable AND no parent


async def analyze_reachability(
    session: AsyncSession, campaign_id: str, start_id: str | None,
) -> ReachabilityReport:
    """BFS the committed TRAVEL graph from the starting location.

    Edges are open `LocationConnection` rows, directed as stored — the same graph
    RouteService actually routes over. Containment (parent_id) is NOT an edge here: the
    router cannot walk a parent link that has no connection, which is exactly why a
    prose import full of parented-but-edgeless rooms plays as unreachable islands. Those
    become real edges in `connect_unreachable` (via the exterior-link repair), and only
    then do they count as reachable.

    A location with no path from the start is unreachable; one that is also parentless
    is an orphan the engine cannot connect on its own.
    """
    locs = list((await session.execute(select(Location).where(
        Location.campaign_id == campaign_id))).scalars())
    by_id = {l.id: l for l in locs}
    if not locs:
        return ReachabilityReport(start_id=start_id)
    root = start_id if start_id in by_id else locs[0].id

    adj: dict[str, set[str]] = {l.id: set() for l in locs}
    conns = list((await session.execute(select(LocationConnection).where(
        LocationConnection.campaign_id == campaign_id))).scalars())
    for e in conns:
        if e.access_state == "open" and e.from_location_id in adj and e.to_location_id in by_id:
            adj[e.from_location_id].add(e.to_location_id)

    seen = {root}
    queue = [root]
    while queue:
        cur = queue.pop()
        for nxt in adj.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)

    unreachable = [l.id for l in locs if l.id not in seen]
    orphans = [lid for lid in unreachable if not by_id[lid].parent_id]
    return ReachabilityReport(start_id=root, reachable=seen,
                              unreachable=unreachable, orphans=orphans)


async def connect_unreachable(
    session: AsyncSession, campaign_id: str, start_id: str | None,
) -> tuple[list[str], list[str]]:
    """Turn authored containment into real travel edges, then report what is still
    stranded.

    Step 1 — give every dead-ended interior a way out. A location with a parent but NO
    committed exit of its own is materialized with the exterior link (child<->parent):
    a prose author who wrote "the plaza, the inn, and the tower are in town" stated that
    connectivity through `parent`, not through exits, and this makes it routable.

    A location that already has a way out is left ALONE — adding a second "outside" link
    to its parent would create a competing exit that hijacks "walk outside" away from
    the destination the author actually authored. So this only ever ADDS a first exit,
    never a redundant one. (This is the exact rule infer_exterior_link was built for; it
    is applied here reachability-first so a chain of parented rooms all connect.)

    Step 2 — measure reachability over the now-complete travel graph. Anything still
    unreachable is a genuinely disconnected island the engine will not connect without
    inventing canon. Those are returned for the owner to fix by hand.

    Returns (inferred_connector_location_ids, remaining_orphan_ids).
    """
    from app.world.route_service import RouteService

    rs = RouteService(session)
    locs = list((await session.execute(select(Location).where(
        Location.campaign_id == campaign_id))).scalars())
    conns = list((await session.execute(select(LocationConnection).where(
        LocationConnection.campaign_id == campaign_id))).scalars())
    out_degree: dict[str, int] = {l.id: 0 for l in locs}
    for e in conns:
        if e.access_state == "open":
            out_degree[e.from_location_id] = out_degree.get(e.from_location_id, 0) + 1

    inferred: list[str] = []
    for loc in locs:
        # Only a location with a parent AND no way out of its own — never one the author
        # already gave an exit.
        if not loc.parent_id or out_degree.get(loc.id, 0) > 0:
            continue
        if await rs.infer_exterior_link(campaign_id=campaign_id, location_id=loc.id):
            inferred.append(loc.id)

    final = await analyze_reachability(session, campaign_id, start_id)
    return inferred, final.unreachable


async def safe_auto_repair(session: AsyncSession, campaign_id: str) -> list[str]:
    """Apply ONLY the SAFE_AUTO_REPAIR class — infer the missing exterior link for any
    interior with a parent but no way out. Returns the repaired location ids. Idempotent
    (re-running finds nothing to do); never touches owner-review or blocking issues."""
    from app.world.route_service import RouteService

    report = await validate_world_graph(session, campaign_id)
    repaired: list[str] = []
    rs = RouteService(session)
    for issue in report.auto_repairable:
        if issue.kind == "missing_exit" and issue.refs:
            link = await rs.infer_exterior_link(campaign_id=campaign_id, location_id=issue.refs[0])
            if link is not None:
                repaired.append(issue.refs[0])
    return repaired
