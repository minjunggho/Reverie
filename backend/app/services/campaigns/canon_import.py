"""Campaign canon import — parse an owner's Markdown/JSON into a structured proposal,
review it (counts + warnings + provenance), then commit canon atomically on approval.

The Markdown is a HUMAN AUTHORING FORMAT; it is never executable state. Parsing is
deterministic (provenance EXPLICITLY_AUTHORED); auto-derived keys are AI_NORMALIZED
and flagged. Nothing becomes canon without the owner's confirmation.

Reused canon models (no duplicate truth): Location + LocationConnection (places &
travel), NPC (characters), Secret (DM secrets), Threat (factions/fronts & world
pressure), CampaignCanonRecord (world-bible facts & clues), Campaign.brief/
central_question/session_prep. See docs/world-canon.md.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError as PydanticValidationError
from sqlalchemy import select

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.campaign import Campaign
from app.models.canon_import import CanonImport
from app.models.enums import Visibility
from app.models.knowledge import Secret
from app.models.location import Location
from app.models.npc import NPC
from app.models.world import Threat
from app.models.world_graph import CampaignCanonRecord, LocationConnection
from app.schemas.belief import (
    BeliefProfile,
    BeliefSource,
    BeliefStance,
    BeliefVisibility,
    DevotionLevel,
    ReligiousRole,
)

ALLOWED_EXTENSIONS = {".json", ".md", ".txt"}
MAX_BYTES = 1_000_000
KEY = r"^[A-Za-z0-9_-]+$"


# --- proposal schema ----------------------------------------------------------------
class ExitProposal(BaseModel):
    to: str = Field(min_length=1, max_length=80)
    label: str = Field(default="", max_length=120)
    direction: str = Field(default="", max_length=40)
    travel_minutes: int = Field(default=0, ge=0, le=100000)
    obvious: bool = True
    one_way: bool = False
    access_state: str = Field(default="open", max_length=20)


class LocationProposal(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=KEY)
    name: str = Field(min_length=1, max_length=160)
    location_type: str = Field(default="LOCATION", max_length=20)
    parent: str | None = Field(default=None, max_length=80)
    obvious: str = Field(default="", max_length=8000)
    focused: str = Field(default="", max_length=8000)
    hidden: str = Field(default="", max_length=8000)
    weather: str = Field(default="", max_length=120)
    current_activity: str = Field(default="", max_length=2000)
    connections: list[str] = Field(default_factory=list, max_length=30)   # legacy simple
    exits: list[ExitProposal] = Field(default_factory=list, max_length=30)


_LEGAL_COMMUNICATION_MODES = {"SPOKEN", "SLATE", "SIGN", "TELEPATHY", "NONVERBAL", "OTHER"}


class NPCProposal(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=KEY)
    name: str = Field(min_length=1, max_length=160)
    personality: str = Field(default="", max_length=2000)
    voice: str = Field(default="", max_length=400)
    goal: str = Field(default="", max_length=2000)
    location: str | None = Field(default=None, max_length=80)   # location key
    communication_mode: str = Field(default="SPOKEN", max_length=20)
    deity_reference: str | None = Field(default=None, max_length=160)
    religious_role: ReligiousRole | None = None
    belief_profile: BeliefProfile | None = None


class FactionProposal(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=KEY)
    name: str = Field(min_length=1, max_length=160)
    goal: str = Field(default="", max_length=2000)
    next_action: str = Field(default="", max_length=2000)
    progress: int = Field(default=0, ge=0, le=100)


class SecretProposal(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=KEY)
    fact: str = Field(min_length=1, max_length=4000)
    clues: list[str] = Field(default_factory=list, max_length=40)


class ThreatProposal(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=KEY)
    name: str = Field(min_length=1, max_length=160)
    goal: str = Field(default="", max_length=2000)
    next_action: str = Field(default="", max_length=2000)
    progress: int = Field(default=0, ge=0, le=100)
    scheduled_minutes: int = Field(default=0, ge=0, le=100000)


class WorldFactProposal(BaseModel):
    fact: str = Field(min_length=1, max_length=4000)
    category: str = Field(default="world_fact", max_length=32)
    visibility: str = Field(default="PUBLIC", max_length=16)


_LEGAL_PROTOCOL_VISIBILITY = {"PUBLIC", "PARTY"}


class ProtocolProposal(BaseModel):
    """An ordered, authored set of rules NPCs/factions hold each other to (e.g. "the
    five rules of the coffin escort"). Order is preserved — never stored as a set —
    so a grounded NPC answer reproduces it verbatim."""
    key: str = Field(min_length=1, max_length=80, pattern=KEY)
    title: str = Field(min_length=1, max_length=200)
    visibility: str = Field(default="PARTY", max_length=16)
    known_by: list[str] = Field(default_factory=list, max_length=50)
    rules: list[str] = Field(min_length=1, max_length=50)


class CampaignProposal(BaseModel):
    version: int = 1
    identity_name: str = Field(default="", max_length=200)
    brief: str = Field(default="", max_length=8000)
    central_question: str = Field(default="", max_length=2000)
    world_facts: list[WorldFactProposal] = Field(default_factory=list, max_length=300)
    locations: list[LocationProposal] = Field(min_length=1, max_length=500)
    factions: list[FactionProposal] = Field(default_factory=list, max_length=100)
    npcs: list[NPCProposal] = Field(default_factory=list, max_length=300)
    secrets: list[SecretProposal] = Field(default_factory=list, max_length=200)
    threats: list[ThreatProposal] = Field(default_factory=list, max_length=100)
    protocols: list[ProtocolProposal] = Field(default_factory=list, max_length=100)
    session_prep: dict = Field(default_factory=dict)
    starting_location: str | None = Field(default=None, max_length=80)


@dataclass
class ImportReview:
    counts: dict
    warnings: list[str]


# --- markdown parsing ---------------------------------------------------------------
def _blocks(text: str) -> list[tuple[str, str]]:
    """Split into ('## Header', body) top-level blocks."""
    parts = re.split(r"(?m)^##\s+", text)
    out = []
    for chunk in parts[1:]:
        lines = chunk.splitlines()
        out.append((lines[0].strip(), "\n".join(lines[1:])))
    return out


def _subfields(body: str) -> dict[str, str]:
    fields, current, buf = {}, "_", []
    for line in body.splitlines():
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            fields[current] = "\n".join(buf).strip()
            current, buf = m.group(1).strip().lower(), []
        else:
            buf.append(line)
    fields[current] = "\n".join(buf).strip()
    return fields


def _bullets(body: str) -> list[str]:
    return [re.sub(r"^[-*]\s*", "", ln).strip() for ln in body.splitlines()
            if ln.strip().startswith(("-", "*"))]


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-").lower() or "x"


def _parse_exits(body: str) -> list[dict]:
    """`- label / direction / minutes -> dest-key`  (any leading part optional)."""
    exits = []
    for ln in _bullets(body):
        if "->" not in ln and "→" not in ln:
            continue
        left, right = re.split(r"->|→", ln, 1)
        to = _slug(right.strip()) if not re.match(KEY, right.strip()) else right.strip()
        parts = [p.strip() for p in left.split("/")]
        label = parts[0] if parts else ""
        direction = parts[1] if len(parts) > 1 else ""
        minutes = 0
        for p in parts:
            mm = re.search(r"(\d+)", p)
            if mm and ("นาที" in p or "min" in p.lower() or p.strip().isdigit()):
                minutes = int(mm.group(1))
        exits.append({"to": to, "label": label, "direction": direction,
                      "travel_minutes": minutes})
    return exits


def _parse_markdown(text: str) -> dict:
    prop: dict = {"version": 1, "locations": [], "world_facts": [], "factions": [],
                  "npcs": [], "secrets": [], "threats": [], "protocols": [],
                  "session_prep": {}}
    m = re.search(r"(?m)^#\s+Campaign:\s*(.+)$", text)
    if m:
        prop["identity_name"] = m.group(1).strip()

    for header, body in _blocks(text):
        low = header.lower()
        if low.startswith("location:"):
            name = header.split(":", 1)[1].strip()
            f = _subfields(body)
            key = f.get("key") or _slug(name)
            exits = _parse_exits(f.get("exits", ""))
            conns = [x.strip() for x in re.split(r"[,\n]", f.get("connections", "")) if x.strip()]
            prop["locations"].append({
                "key": key, "name": name, "location_type": (f.get("type") or "LOCATION").upper(),
                "parent": (f.get("parent") or None), "obvious": f.get("obvious", ""),
                "focused": f.get("focused", ""), "hidden": f.get("hidden", ""),
                "weather": f.get("weather", ""), "current_activity": f.get("activity", ""),
                "connections": conns, "exits": exits})
        elif low.startswith("npc:"):
            name = header.split(":", 1)[1].strip()
            f = _subfields(body)
            prop["npcs"].append({"key": f.get("key") or _slug(name), "name": name,
                                 "personality": f.get("personality", ""), "voice": f.get("voice", ""),
                                 "goal": f.get("goal", ""),
                                 "location": (f.get("location") or None),
                                 "communication_mode": (f.get("communication") or "SPOKEN").upper(),
                                 "deity_reference": (f.get("deity") or None),
                                 "religious_role": (
                                     f.get("religious role", "").strip().upper() or None
                                 )})
        elif low.startswith("protocol:"):
            title = header.split(":", 1)[1].strip()
            f = _subfields(body)
            prop.setdefault("protocols", []).append({
                "key": f.get("key") or _slug(title), "title": title,
                "visibility": (f.get("visibility") or "PARTY").strip().upper(),
                "known_by": _bullets(f.get("known by", "")),
                "rules": _bullets(f.get("rules", "")),
            })
        elif low.startswith("faction:"):
            name = header.split(":", 1)[1].strip()
            f = _subfields(body)
            prop["factions"].append({"key": f.get("key") or _slug(name), "name": name,
                                     "goal": f.get("goal", ""), "next_action": f.get("methods", "") or f.get("next action", ""),
                                     "progress": _int(f.get("progress", "0"))})
        elif low.startswith("secret:"):
            name = header.split(":", 1)[1].strip()
            f = _subfields(body)
            prop["secrets"].append({"key": f.get("key") or _slug(name),
                                    "fact": f.get("_", "") or f.get("truth", "") or name,
                                    "clues": _bullets(f.get("clues", ""))})
        elif low.startswith("threat:"):
            name = header.split(":", 1)[1].strip()
            f = _subfields(body)
            prop["threats"].append({"key": f.get("key") or _slug(name), "name": name,
                                    "goal": f.get("goal", ""), "next_action": f.get("next action", ""),
                                    "progress": _int(f.get("progress", "0")),
                                    "scheduled_minutes": _int(f.get("scheduled", "0"))})
        elif low in ("brief", "player brief", "player-safe brief"):
            prop["brief"] = body.strip()
        elif low in ("central question", "central dramatic question"):
            prop["central_question"] = body.strip()
        elif low in ("world facts", "known world", "world bible"):
            prop["world_facts"] += [{"fact": b, "category": "world_fact", "visibility": "PUBLIC"}
                                    for b in _bullets(body)]
        elif low in ("session 1", "session preparation", "session prep"):
            f = _subfields(body)
            prop["session_prep"] = {
                "purpose": f.get("purpose", "") or f.get("session purpose", ""),
                "opening_location": (f.get("opening location") or f.get("opening_location") or None),
                "present_npcs": _bullets(f.get("present npcs", "")),
                "current_activity": f.get("current activity", ""),
                "allowed_clues": _bullets(f.get("allowed clues", "")),
                "protected_secrets": _bullets(f.get("protected secrets", "")),
                "do_not_reveal": _bullets(f.get("do not reveal", "")),
            }
            if prop["session_prep"].get("opening_location"):
                prop["starting_location"] = _slug(prop["session_prep"]["opening_location"])

    if not prop["locations"]:
        raise ValidationError("campaign needs at least one '## Location: Name' section")
    return prop


def _int(s: str) -> int:
    m = re.search(r"-?\d+", s or "")
    return int(m.group(0)) if m else 0


# --- top-level parse + validation ----------------------------------------------------
def parse_campaign_file(filename: str, data: bytes) -> tuple[str, CampaignProposal, ImportReview]:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError("supported campaign files are .json, .md, and .txt")
    if not data or len(data) > MAX_BYTES:
        raise ValidationError("campaign file must be between 1 byte and 1 MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("campaign file must be UTF-8 text") from exc
    try:
        raw = json.loads(text) if ext == ".json" else _parse_markdown(text)
        proposal = CampaignProposal.model_validate(raw)
    except (json.JSONDecodeError, PydanticValidationError) as exc:
        raise ValidationError(f"invalid campaign structure: {exc}") from exc

    review = _validate(proposal)
    return text, proposal, review


def _validate(p: CampaignProposal) -> ImportReview:
    keys = [x.key for x in p.locations]
    if len(keys) != len(set(keys)):
        raise ValidationError("location keys must be unique")
    known = set(keys)

    def _refs_ok(ref: str) -> bool:
        return ref in known

    bad = sorted({c for loc in p.locations for c in loc.connections if not _refs_ok(c)}
                 | {e.to for loc in p.locations for e in loc.exits if not _refs_ok(e.to)}
                 | {loc.parent for loc in p.locations if loc.parent and not _refs_ok(loc.parent)})
    if bad:
        raise ValidationError(f"connections/exits/parents reference unknown location keys: {', '.join(bad)}")

    warnings: list[str] = []
    for n in p.npcs:
        if not n.goal:
            warnings.append(f"NPC '{n.name}' has no goal.")
        if n.location and n.location not in known:
            warnings.append(f"NPC '{n.name}' references unknown location '{n.location}'.")
        elif not n.location:
            warnings.append(f"NPC '{n.name}' has no canonical current location.")
        if n.communication_mode not in _LEGAL_COMMUNICATION_MODES:
            warnings.append(f"NPC '{n.name}' has unknown communication mode "
                            f"'{n.communication_mode}' — defaulting to SPOKEN.")
    for f in p.factions:
        if not f.goal:
            warnings.append(f"Faction '{f.name}' has no goal.")
    for sec in p.secrets:
        if len(sec.clues) < 2:
            warnings.append(f"Secret '{sec.key}' has only {len(sec.clues)} revelation path(s).")
    if p.starting_location and p.starting_location not in known:
        warnings.append(f"Session prep opening_location '{p.starting_location}' is not a known location.")

    proto_keys = [pr.key for pr in p.protocols]
    if len(proto_keys) != len(set(proto_keys)):
        raise ValidationError("protocol keys must be unique")
    known_npc_names = {n.name for n in p.npcs}
    for pr in p.protocols:
        if pr.visibility not in _LEGAL_PROTOCOL_VISIBILITY:
            raise ValidationError(
                f"protocol '{pr.key}' has illegal visibility {pr.visibility!r} "
                f"(must be one of {sorted(_LEGAL_PROTOCOL_VISIBILITY)})")
        for name in pr.known_by:
            if name not in known_npc_names:
                warnings.append(f"Protocol '{pr.title}' names unknown NPC '{name}' in Known By.")

    counts = {
        "identity": 1 if p.identity_name else 0,
        "player_safe_brief": 1 if p.brief else 0,
        "world_facts": len(p.world_facts),
        "locations": len(p.locations),
        "factions": len(p.factions),
        "important_npcs": len(p.npcs),
        "secrets": len(p.secrets),
        "clues": sum(len(s.clues) for s in p.secrets),
        "threats": len(p.threats),
        "protocols": len(p.protocols),
        "session_prep": 1 if p.session_prep else 0,
    }
    return ImportReview(counts=counts, warnings=warnings)


# --- service -------------------------------------------------------------------------
class CanonImportService:
    def __init__(self, session) -> None:
        self.session = session

    async def create_draft(self, *, campaign_id: str, uploader_member_id: str,
                           filename: str, data: bytes) -> CanonImport:
        text, proposal, review = parse_campaign_file(filename, data)
        digest = hashlib.sha256(data).hexdigest()
        duplicate = (await self.session.execute(
            select(CanonImport).where(CanonImport.campaign_id == campaign_id,
                                      CanonImport.content_sha256 == digest,
                                      CanonImport.status != "REJECTED"))).scalars().first()
        if duplicate:
            raise ConflictError(f"this exact file is already import {duplicate.id}")
        row = CanonImport(
            campaign_id=campaign_id, uploader_member_id=uploader_member_id,
            filename=filename, content_sha256=digest, source_text=text,
            proposal=proposal.model_dump(mode="json"), errors=review.warnings,
            status="PENDING_REVIEW",
        )
        row.proposal = {**row.proposal, "_review": {"counts": review.counts,
                                                    "warnings": review.warnings}}
        self.session.add(row)
        await self.session.flush()
        return row

    async def create_ai_draft(self, *, campaign_id: str, uploader_member_id: str,
                              premise: str, proposal: CampaignProposal) -> CanonImport:
        """Store an AI-generated world proposal for owner review. Identical review/
        approve lifecycle as a file import; provenance is AI_PROPOSED so committed
        canon stays marked as machine-proposed (owner-approved), never as authored."""
        review = _validate(proposal)
        payload = proposal.model_dump(mode="json")
        digest = hashlib.sha256(
            (premise + json.dumps(payload, sort_keys=True)).encode("utf-8")).hexdigest()
        row = CanonImport(
            campaign_id=campaign_id, uploader_member_id=uploader_member_id,
            filename="ai-campaign-proposal.json", content_sha256=digest,
            source_text=premise,
            proposal={**payload, "_review": {"counts": review.counts,
                                             "warnings": review.warnings},
                      "_source": "AI_PROPOSED"},
            status="PENDING_REVIEW",
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(self, import_id: str, campaign_id: str) -> CanonImport:
        row = await self.session.get(CanonImport, import_id)
        if row is None or row.campaign_id != campaign_id:
            raise NotFoundError("campaign import not found")
        return row

    async def approve(self, *, import_id: str, campaign_id: str) -> ImportReview:
        row = await self.get(import_id, campaign_id)
        if row.status != "PENDING_REVIEW":
            raise ConflictError(f"import is already {row.status.lower()}")
        proposal = CampaignProposal.model_validate(
            {k: v for k, v in row.proposal.items() if k not in ("_review", "_source")})
        # Explicit imported canon outranks AI-generated content — committed rows carry
        # which one they are so later contradictions are decidable.
        loc_provenance = ("AI_PROPOSED_CANON" if row.proposal.get("_source") == "AI_PROPOSED"
                          else "IMPORTED")
        record_provenance = ("AI_PROPOSED_CANON" if loc_provenance == "AI_PROPOSED_CANON"
                             else "IMPORTED_CANON")

        existing = set((await self.session.execute(
            select(Location.name).where(Location.campaign_id == campaign_id))).scalars())
        conflicts = [p.name for p in proposal.locations if p.name in existing]
        if conflicts:
            raise ConflictError("location names already exist: " + ", ".join(conflicts))

        # 1. locations (two passes: create, then link parents + connections/exits).
        by_key: dict[str, Location] = {}
        for item in proposal.locations:
            loc = Location(
                campaign_id=campaign_id, name=item.name,
                description_obvious=item.obvious, description_focused=item.focused,
                description_hidden=item.hidden, location_type=item.location_type,
                weather=item.weather, current_activity=item.current_activity,
                provenance=loc_provenance, connections=[],
                state={"canon_import_id": row.id, "source_key": item.key})
            self.session.add(loc)
            await self.session.flush()
            by_key[item.key] = loc
        graph = _Graph(self.session)
        for item in proposal.locations:
            loc = by_key[item.key]
            if item.parent:
                loc.parent_id = by_key[item.parent].id
            # Legacy simple connections → 0-minute obvious bidirectional edges + mirror.
            loc.connections = [f"location:{by_key[k].id}" for k in item.connections]
            for k in item.connections:
                await graph.edge(campaign_id, loc.id, by_key[k].id, obvious=True)
            for e in item.exits:
                await graph.edge(campaign_id, loc.id, by_key[e.to].id, label=e.label,
                                 direction=e.direction, travel_minutes=e.travel_minutes,
                                 obvious=e.obvious, one_way=e.one_way,
                                 access_state=e.access_state)

        # 2. world facts + clues → CampaignCanonRecord.
        for wf in proposal.world_facts:
            self.session.add(CampaignCanonRecord(
                campaign_id=campaign_id, category=wf.category, fact=wf.fact,
                visibility=wf.visibility, provenance=record_provenance, importance=20))

        # 3. secrets (DM) + their clues (DM-scoped canon records).
        secret_by_key: dict[str, Secret] = {}
        for sec in proposal.secrets:
            s = Secret(campaign_id=campaign_id, fact=sec.fact, visibility=Visibility.DM_ONLY.value)
            self.session.add(s)
            await self.session.flush()
            secret_by_key[sec.key] = s
            for clue in sec.clues:
                self.session.add(CampaignCanonRecord(
                    campaign_id=campaign_id, category="clue", fact=clue,
                    visibility=Visibility.DM_ONLY.value, provenance=record_provenance,
                    scope_type="secret", scope_id=s.id, importance=30))

        # 4. NPCs at their canonical location.
        for n in proposal.npcs:
            mode = n.communication_mode if n.communication_mode in _LEGAL_COMMUNICATION_MODES else "SPOKEN"
            npc = NPC(
                campaign_id=campaign_id, name=n.name, personality=n.personality,
                voice_register=n.voice, goals=[n.goal] if n.goal else [],
                current_location_id=by_key[n.location].id if n.location and n.location in by_key else None,
                communication_mode=mode)
            self.session.add(npc)
            await self.session.flush()
            profile = n.belief_profile
            if profile is not None:
                profile = profile.model_copy(update={
                    "source": BeliefSource.IMPORTED_CANON,
                    "provenance": f"CANON_IMPORT:{row.id}:{n.key}",
                })
            elif n.deity_reference or n.religious_role:
                from app.npcs.belief_generator import knowledge_for_role
                from app.services.faith import FaithService

                deity_key = None
                if n.deity_reference:
                    resolution = await FaithService(self.session).resolve_deity_reference(
                        campaign_id, n.deity_reference
                    )
                    if resolution.deity_key is None:
                        raise ConflictError(
                            f"imported NPC '{n.name}' deity {n.deity_reference!r} "
                            "does not resolve uniquely in an active pantheon"
                        )
                    deity_key = resolution.deity_key
                profile = BeliefProfile(
                    primary_deity_key=deity_key,
                    stance=BeliefStance.DEVOUT if n.religious_role else BeliefStance.BELIEVER,
                    devotion=DevotionLevel.DEVOUT if n.religious_role else DevotionLevel.ORDINARY,
                    visibility=BeliefVisibility.PUBLIC,
                    religious_role=n.religious_role,
                    knowledge_level=knowledge_for_role(n.religious_role),
                    source=BeliefSource.IMPORTED_CANON,
                    provenance=f"CANON_IMPORT:{row.id}:{n.key}",
                )
            if profile is not None:
                from app.services.beliefs import BeliefService

                await BeliefService(self.session).set_npc_belief(npc, profile)

        # 5. factions + threats → world-pressure fronts (Threat).
        for f in list(proposal.factions):
            self.session.add(Threat(campaign_id=campaign_id, name=f.name, goal=f.goal,
                                    next_action=f.next_action, progress=f.progress,
                                    status="active"))
        for t in proposal.threats:
            self.session.add(Threat(campaign_id=campaign_id, name=t.name, goal=t.goal,
                                    next_action=t.next_action, progress=t.progress,
                                    scheduled_game_time=t.scheduled_minutes, status="active"))

        # 5b. ordered protocols (structured, never an unordered fact bag).
        for pr in proposal.protocols:
            self.session.add(CampaignCanonRecord(
                campaign_id=campaign_id, category="protocol", fact=pr.title,
                visibility=pr.visibility, provenance=record_provenance, importance=25,
                data={"key": pr.key, "rules": list(pr.rules), "known_by": list(pr.known_by)}))

        # 6. campaign-level canon + session prep + starting location.
        campaign = await self.session.get(Campaign, campaign_id)
        if campaign is not None:
            if proposal.brief:
                campaign.brief = proposal.brief
            if proposal.central_question:
                campaign.central_question = proposal.central_question
            prep = dict(proposal.session_prep or {})
            if proposal.starting_location and proposal.starting_location in by_key:
                prep["opening_location_id"] = by_key[proposal.starting_location].id
                # Canonical anchor: explicit imported starting location (E7).
                campaign.starting_location_id = by_key[proposal.starting_location].id
            campaign.session_prep = prep
            # Seed main-story continuity so the central storyline is remembered and
            # keeps reacting across turns/restarts (never lost, never railroaded).
            from app.services.campaigns.main_story import MainStoryService

            await MainStoryService(self.session).initialize_from_proposal(
                campaign_id, proposal)

        row.status = "APPROVED"
        review = _validate(proposal)
        return review

    async def repair_protocols(self, *, import_id: str, campaign_id: str) -> dict:
        """Idempotent, protocol-only backfill for an already-approved campaign.

        Re-parses THIS draft's stored source text for `## Protocol:` blocks only and
        adds any record whose `data.key` isn't already canon — never touching
        locations/NPCs/secrets/threats, so it can never hit the duplicate-location
        conflict `approve()` would raise on a re-import of a revised file. The owner
        workflow: re-upload the revised campaign file (a new draft, since the
        content hash changed), then repair THAT draft instead of approving it."""
        row = await self.get(import_id, campaign_id)
        if row.status == "REJECTED":
            raise ConflictError("cannot repair a rejected import")
        _, proposal, _ = parse_campaign_file(row.filename, row.source_text.encode("utf-8"))
        if not proposal.protocols:
            return {"protocols_added": 0}

        existing_keys = set()
        rows = (await self.session.execute(select(CampaignCanonRecord).where(
            CampaignCanonRecord.campaign_id == campaign_id,
            CampaignCanonRecord.category == "protocol"))).scalars()
        for r in rows:
            key = (r.data or {}).get("key")
            if key:
                existing_keys.add(key)

        added = 0
        for pr in proposal.protocols:
            if pr.key in existing_keys:
                continue
            self.session.add(CampaignCanonRecord(
                campaign_id=campaign_id, category="protocol", fact=pr.title,
                visibility=pr.visibility, provenance="IMPORTED_CANON", importance=25,
                data={"key": pr.key, "rules": list(pr.rules), "known_by": list(pr.known_by)}))
            existing_keys.add(pr.key)
            added += 1
        return {"protocols_added": added}

    async def reject(self, *, import_id: str, campaign_id: str) -> CanonImport:
        row = await self.get(import_id, campaign_id)
        if row.status != "PENDING_REVIEW":
            raise ConflictError(f"import is already {row.status.lower()}")
        row.status = "REJECTED"
        return row


class _Graph:
    def __init__(self, session):
        self.session = session

    async def edge(self, campaign_id, frm, to, *, label="", direction="",
                   travel_minutes=0, obvious=True, one_way=False, access_state="open"):
        self.session.add(LocationConnection(
            campaign_id=campaign_id, from_location_id=frm, to_location_id=to,
            label=label, direction=direction, travel_minutes=travel_minutes,
            obvious=obvious, one_way=one_way, access_state=access_state))
        if not one_way:
            self.session.add(LocationConnection(
                campaign_id=campaign_id, from_location_id=to, to_location_id=frm,
                label="กลับ", direction="", travel_minutes=travel_minutes,
                obvious=obvious, access_state=access_state))
        await self.session.flush()
