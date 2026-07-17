"""ClueService — learning a clue CHANGES the world.

Before this, revealing a clue produced prose and nothing else: the fragment was
narrated, an event was recorded, and no route opened, no place became reachable, no
objective became known. Clues were free text in three places, linked to nothing
(docs/progression-audit.md, RC3).

`discover` marks a clue learned and applies its authored `reveals` through the
services that already own those mutations — Location.discovery_state, RouteService /
ConsequenceService.discover_route, Quest state. This layer adds an EDGE, never a
second way to mutate the world.

Discovery is idempotent: a clue learned twice applies its reveals once. Two players
reading the same page must not double-fire the world.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.campaign import Campaign
from app.models.campaign_progression import Clue
from app.models.consequences import Quest
from app.models.enums import Visibility
from app.models.location import Location
from app.models.world_graph import CampaignCanonRecord, LocationConnection

log = get_logger(__name__)


@dataclass
class ClueEffect:
    """What discovering a clue actually opened up. Empty = it was already known."""

    clue_key: str = ""
    already_known: bool = False
    revealed_locations: list[str] = field(default_factory=list)   # location names
    revealed_routes: list[str] = field(default_factory=list)      # "A → B"
    revealed_objectives: list[str] = field(default_factory=list)  # quest keys
    revealed_facts: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)           # refs that missed

    @property
    def opened_anything(self) -> bool:
        return bool(self.revealed_locations or self.revealed_routes
                    or self.revealed_objectives or self.revealed_facts)


class ClueService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_key(self, campaign_id: str, key: str) -> Clue | None:
        return (await self.session.execute(
            select(Clue).where(Clue.campaign_id == campaign_id, Clue.key == key)
        )).scalars().first()

    async def match_text(self, campaign_id: str, text: str) -> Clue | None:
        """Find the authored clue a narrated fragment corresponds to.

        The reveal path speaks in fragments ("...ไม่ใช่ของมนุษย์"), not keys, and a
        fragment is often a substring of the full clue. Containment either way, longest
        first, so a specific clue wins over a broader one that merely contains it.
        """
        needle = (text or "").strip()
        if not needle:
            return None
        rows = (await self.session.execute(
            select(Clue).where(Clue.campaign_id == campaign_id)
        )).scalars().all()
        hits = [c for c in rows if needle in c.text or c.text in needle]
        if not hits:
            return None
        return sorted(hits, key=lambda c: len(c.text), reverse=True)[0]

    async def discover(
        self, *, campaign_id: str, clue: Clue, session_id: str | None = None,
        scene_id: str | None = None, actor_entity: str | None = None,
    ) -> ClueEffect:
        """Learn a clue and apply everything it opens. Idempotent."""
        effect = ClueEffect(clue_key=clue.key)
        if clue.discovered:
            effect.already_known = True
            return effect

        campaign = await self.session.get(Campaign, campaign_id)
        clue.discovered = True
        clue.discovered_game_time = campaign.current_game_time if campaign else 0

        for edge in list(clue.reveals or []):
            kind, ref = (edge or {}).get("kind"), (edge or {}).get("ref")
            if not kind or not ref:
                continue
            try:
                await self._apply(campaign_id, kind, ref, edge, effect,
                                  session_id=session_id, scene_id=scene_id,
                                  actor_entity=actor_entity)
            except Exception as exc:   # a bad edge must never break the player's turn
                log.warning("clue %s: reveal %s->%s failed: %s", clue.key, kind, ref, exc)
                effect.unresolved.append(f"{kind}:{ref}")

        await self.session.flush()
        return effect

    async def _apply(
        self, campaign_id: str, kind: str, ref: str, edge: dict, effect: ClueEffect,
        *, session_id, scene_id, actor_entity,
    ) -> None:
        if kind == "location":
            loc = await self._location(campaign_id, ref)
            if loc is None:
                effect.unresolved.append(f"location:{ref}")
                return
            # A place the party had no way to know about becomes a real destination.
            if (loc.discovery_state or "KNOWN") != "KNOWN":
                loc.discovery_state = "KNOWN"
                effect.revealed_locations.append(loc.name)

        elif kind == "route":
            # ref is "from_key->to_key": a clue opens a specific connection.
            src_ref, _, dst_ref = ref.partition("->")
            src = await self._location(campaign_id, src_ref.strip())
            dst = await self._location(campaign_id, dst_ref.strip())
            if src is None or dst is None:
                effect.unresolved.append(f"route:{ref}")
                return
            edges = (await self.session.execute(
                select(LocationConnection).where(
                    LocationConnection.campaign_id == campaign_id,
                    LocationConnection.from_location_id.in_([src.id, dst.id]),
                    LocationConnection.to_location_id.in_([src.id, dst.id]),
                )
            )).scalars().all()
            if not edges:
                effect.unresolved.append(f"route:{ref}")
                return
            # Report only what actually CHANGED — checked before the mutation, since
            # after it every edge is KNOWN and "did this open anything" is unanswerable.
            changed = False
            for e in edges:
                if (e.discovery_state or "KNOWN") != "KNOWN":
                    e.discovery_state = "KNOWN"
                    changed = True
            if changed:
                effect.revealed_routes.append(f"{src.name} → {dst.name}")

        elif kind == "objective":
            quest = (await self.session.execute(
                select(Quest).where(Quest.campaign_id == campaign_id, Quest.key == ref)
            )).scalars().first()
            if quest is None:
                effect.unresolved.append(f"objective:{ref}")
                return
            # Only UNKNOWN advances. A clue must never reset an objective the party is
            # already working on, or has already finished, back to "newly discovered".
            if quest.state == "UNKNOWN":
                quest.state = "DISCOVERED"
                effect.revealed_objectives.append(quest.key)

        elif kind == "fact":
            # The clue's ref IS the fact text — a clue that establishes something the
            # party now simply knows. PARTY-visible by construction.
            self.session.add(CampaignCanonRecord(
                campaign_id=campaign_id, category="clue", fact=ref,
                visibility=Visibility.PARTY.value, provenance="CLUE_DISCOVERED",
                importance=30,
            ))
            effect.revealed_facts.append(ref)

        elif kind in ("npc", "secret"):
            # Pointers, not reveals. A clue can tell the party WHO to look for or that
            # a secret exists; learning the secret itself stays on the reveal_secret
            # path, which is privately delivered and authored.
            pass

    async def _location(self, campaign_id: str, ref: str) -> Location | None:
        """Resolve a clue's location ref.

        The importer resolves refs to ids at commit time, so the id path is the normal
        one. The authored-key and name paths cover hand-written and runtime-created
        clues, whose refs are whatever a human typed. `include_hidden` is required:
        the whole point of a clue is to reach a place that is NOT yet routable.
        """
        if not ref:
            return None
        row = await self.session.get(Location, ref)
        if row is not None and row.campaign_id == campaign_id:
            return row
        # An authored import key — the importer records it on Location.state.
        rows = (await self.session.execute(
            select(Location).where(Location.campaign_id == campaign_id)
        )).scalars().all()
        for loc in rows:
            if (loc.state or {}).get("source_key") == ref:
                return loc
        # Last resort: a human-written name or alias.
        from app.world.location_resolver import LocationResolver

        result = await LocationResolver(self.session).resolve(
            campaign_id=campaign_id, reference=ref, include_hidden=True)
        return result.match.location if result.match else None
