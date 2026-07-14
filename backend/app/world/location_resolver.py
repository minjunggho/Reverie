"""LocationResolver — one authoritative reference → location resolver.

Conservative name matching ("is the stored name a substring of what they typed?")
fails the moment the player and the canon use different languages: "ไปมหาวิหาร"
never matches an English-named "Cathedral District". This resolver matches a movement
reference against every name a place answers to — canonical, Thai, English, and
owner/import aliases — using the SAME `normalize_choice_name` the rest of the engine
uses (no second, divergent normalizer). It also resolves NPC-directed goals
("ไปหายามเฝ้าประตู") to where that NPC is believed to be, and it never offers a HIDDEN
or SECRET place as a navigation target.

It resolves or asks — it never guesses between two equally-good matches, and it never
invents a place. Ambiguity comes back as a focused clarification for the caller.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.location import Location
from app.models.npc import NPC
from app.rules_content.choice_names import normalize_choice_name

# Discovery states that must never be offered as a navigation target to a player.
_UNROUTABLE_DISCOVERY = frozenset({"HIDDEN", "SECRET"})


def _norm(s: str) -> str:
    return normalize_choice_name(s or "")


def location_names(loc: Location) -> list[str]:
    """Every name a location answers to, canonical first."""
    names = [loc.name, loc.name_th, loc.name_en, *(loc.aliases or [])]
    seen: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.append(n)
    return seen


@dataclass
class LocationMatch:
    location: Location
    confidence: float
    via: str            # name | name_th | name_en | alias | substring | npc


@dataclass
class ResolveResult:
    match: LocationMatch | None = None
    ambiguous: list[Location] = field(default_factory=list)
    npc: NPC | None = None          # set when the reference named an NPC
    npc_location_unknown: bool = False   # NPC found but its whereabouts are unknown

    @property
    def resolved(self) -> bool:
        return self.match is not None

    @property
    def is_ambiguous(self) -> bool:
        return len(self.ambiguous) > 1


class LocationResolver:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _routable_locations(
        self, campaign_id: str, *, include_hidden: bool, exclude_id: str | None,
    ) -> list[Location]:
        rows = (await self.session.execute(select(Location).where(
            Location.campaign_id == campaign_id))).scalars()
        out: list[Location] = []
        for loc in rows:
            if loc.id == exclude_id:
                continue
            if not include_hidden and (loc.discovery_state or "KNOWN") in _UNROUTABLE_DISCOVERY:
                continue
            out.append(loc)
        return out

    async def resolve(
        self, *, campaign_id: str, reference: str, exclude_id: str | None = None,
        include_hidden: bool = False,
    ) -> ResolveResult:
        """Resolve a reference to a single location, an ambiguity, or nothing. Exact
        (normalized) name/alias equality wins; a conservative substring is the
        lower-confidence fallback. HIDDEN/SECRET places are never offered."""
        ref = _norm(reference)
        if not ref:
            return ResolveResult()
        locs = await self._routable_locations(
            campaign_id, include_hidden=include_hidden, exclude_id=exclude_id)

        # Tier 1 — exact normalized equality against any name the place answers to.
        exact: list[tuple[Location, str]] = []
        for loc in locs:
            for i, nm in enumerate(location_names(loc)):
                if _norm(nm) == ref:
                    via = ("name", "name_th", "name_en")[i] if i < 3 else "alias"
                    exact.append((loc, via))
                    break
        if len(exact) == 1:
            loc, via = exact[0]
            return ResolveResult(LocationMatch(loc, 0.97, via))
        if len(exact) > 1:
            return ResolveResult(ambiguous=[l for l, _ in exact])

        # Tier 2 — conservative substring either way, longest name wins; a tie is
        # ambiguous (never a coin-flip).
        subs: list[tuple[Location, int]] = []
        for loc in locs:
            best_len = 0
            for nm in location_names(loc):
                n = _norm(nm)
                if n and (n in ref or ref in n):
                    best_len = max(best_len, len(n))
            if best_len:
                subs.append((loc, best_len))
        if subs:
            subs.sort(key=lambda t: -t[1])
            top_len = subs[0][1]
            top = [loc for loc, ln in subs if ln == top_len]
            if len(top) == 1:
                return ResolveResult(LocationMatch(top[0], 0.8, "substring"))
            return ResolveResult(ambiguous=top)

        # Tier 3 — an NPC-directed goal: route to where the NPC is believed to be.
        npc = await self._resolve_npc(campaign_id, ref)
        if npc is not None:
            if npc.current_location_id:
                loc = await self.session.get(Location, npc.current_location_id)
                if loc is not None and (
                        include_hidden or (loc.discovery_state or "KNOWN") not in _UNROUTABLE_DISCOVERY):
                    return ResolveResult(LocationMatch(loc, 0.85, "npc"), npc=npc)
            return ResolveResult(npc=npc, npc_location_unknown=True)

        return ResolveResult()

    async def _resolve_npc(self, campaign_id: str, ref: str) -> NPC | None:
        """Find the single NPC named in the reference (exact name, else a unique
        substring). None on no match or ambiguity — never a guess."""
        rows = list((await self.session.execute(select(NPC).where(
            NPC.campaign_id == campaign_id))).scalars())
        exact = [n for n in rows if _norm(n.name) == ref]
        if len(exact) == 1:
            return exact[0]
        subs = [(n, len(_norm(n.name))) for n in rows
                if _norm(n.name) and _norm(n.name) in ref]
        if not subs:
            return None
        subs.sort(key=lambda t: -t[1])
        top_len = subs[0][1]
        top = [n for n, ln in subs if ln == top_len]
        return top[0] if len(top) == 1 else None
