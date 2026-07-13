"""Deterministic campaign validation — engine judgement, never the LLM.

Runs over a parsed/extracted `CampaignProposal` and reports typed issues so the
owner sees exactly what is wrong before anything becomes canon. Structural
impossibilities are ERRORs (block commit); soft gaps are WARNINGs (surface, allow).
Contradictions are surfaced, never auto-resolved.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class ValidationIssue:
    kind: str            # missing_start | unreachable | duplicate_identity | broken_ref | ...
    severity: str        # "error" | "warning"
    message: str
    refs: list[str] = field(default_factory=list)   # affected keys/names

    def as_dict(self) -> dict:
        return {"kind": self.kind, "severity": self.severity,
                "message": self.message, "refs": list(self.refs)}


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, kind, severity, message, refs=None) -> None:
        self.issues.append(ValidationIssue(kind, severity, message, list(refs or [])))


def validate_campaign(proposal) -> ValidationResult:
    """Deterministic checks over a CampaignProposal (or a dict-like with the same
    fields). Complements the parser's hard structural raises with the richer,
    reviewable checks the spec calls for."""
    res = ValidationResult()
    locations = list(getattr(proposal, "locations", []) or [])
    loc_keys = [l.key for l in locations]
    known = set(loc_keys)

    # duplicate location keys (identity collision)
    _dupes(res, loc_keys, "location")
    # duplicate NPC / faction identities
    _dupes(res, [n.key for n in getattr(proposal, "npcs", []) or []], "npc")
    _dupes(res, [f.key for f in getattr(proposal, "factions", []) or []], "faction")
    _dupes(res, [s.key for s in getattr(proposal, "secrets", []) or []], "secret")

    # broken references (connections/exits/parents)
    for loc in locations:
        for c in loc.connections:
            if c not in known:
                res.add("broken_ref", "error",
                        f"location '{loc.key}' connects to unknown '{c}'", [loc.key, c])
        for e in loc.exits:
            if e.to not in known:
                res.add("broken_ref", "error",
                        f"location '{loc.key}' has an exit to unknown '{e.to}'", [loc.key, e.to])
        if loc.parent and loc.parent not in known:
            res.add("broken_ref", "error",
                    f"location '{loc.key}' has unknown parent '{loc.parent}'", [loc.key, loc.parent])

    # missing / invalid starting location
    start = getattr(proposal, "starting_location", None)
    if not locations:
        res.add("missing_start", "error", "campaign has no locations at all", [])
    elif not start:
        res.add("missing_start", "error",
                "campaign has no starting location — Session 1 has nowhere to open", [])
    elif start not in known:
        res.add("missing_start", "error",
                f"starting location '{start}' is not a defined location", [start])

    # reachability: every location must be reachable from the start (via the
    # undirected connection/exit graph). An important place the party can never
    # get to is a dead campaign branch.
    if start and start in known and len(known) > 1:
        reachable = _reachable_from(start, locations)
        stranded = sorted(known - reachable)
        if stranded:
            res.add("unreachable", "error",
                    f"{len(stranded)} location(s) unreachable from the start: "
                    f"{', '.join(stranded)}", stranded)

    # NPCs: motives + valid location
    for n in getattr(proposal, "npcs", []) or []:
        if not (n.goal or "").strip():
            res.add("npc_no_motive", "warning", f"NPC '{n.name}' has no goal/motive", [n.key])
        if n.location and n.location not in known:
            res.add("broken_ref", "error",
                    f"NPC '{n.name}' is at unknown location '{n.location}'", [n.key, n.location])

    # secrets need a clue path (at least one) — a secret with zero clues can never
    # be discovered.
    for s in getattr(proposal, "secrets", []) or []:
        if not s.clues:
            res.add("secret_no_clue", "error",
                    f"secret '{s.key}' has no clue path — players can never find it", [s.key])
        elif len(s.clues) < 2:
            res.add("secret_thin_clue", "warning",
                    f"secret '{s.key}' has only 1 clue path (fragile)", [s.key])

    # main quest / threat without a lead: a threat that drives the story but has no
    # next_action and no scheduled time is inert — nothing will ever push it.
    for t in getattr(proposal, "threats", []) or []:
        if not (t.next_action or "").strip() and not getattr(t, "scheduled_minutes", 0):
            res.add("threat_no_lead", "warning",
                    f"threat '{t.name}' has no next action and no schedule — it will never advance",
                    [t.key])

    return res


def _dupes(res: ValidationResult, keys: list[str], kind: str) -> None:
    seen, dup = set(), set()
    for k in keys:
        (dup if k in seen else seen).add(k)
    for k in sorted(dup):
        res.add("duplicate_identity", "error",
                f"duplicate {kind} key '{k}'", [k])


def _reachable_from(start: str, locations) -> set[str]:
    adj: dict[str, set[str]] = {l.key: set() for l in locations}
    for l in locations:
        for c in l.connections:
            adj.setdefault(l.key, set()).add(c)
            adj.setdefault(c, set()).add(l.key)          # treat as undirected
        for e in l.exits:
            adj.setdefault(l.key, set()).add(e.to)
            adj.setdefault(e.to, set()).add(l.key)
        if l.parent:
            adj.setdefault(l.key, set()).add(l.parent)
            adj.setdefault(l.parent, set()).add(l.key)
    seen, q = {start}, deque([start])
    while q:
        cur = q.popleft()
        for nxt in adj.get(cur, ()):  # noqa: SIM118
            if nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    return seen
