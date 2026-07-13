"""Canon provenance ladder + priority — the one authority on "who wins."

Every canonical fact carries a provenance. When two facts about the same thing
disagree, the higher-priority provenance wins — EXCEPT the engine never silently
resolves a contradiction between two owner-authored facts; those are surfaced for
review. Explicit owner canon outranks all AI content, always.
"""
from __future__ import annotations

# Highest authority first. `PRIORITY[p]` is larger for stronger canon.
PROVENANCE_LADDER = (
    "IMPORTED_EXPLICIT",     # the owner wrote it verbatim in the import
    "OWNER_EDITED",          # the owner edited it in review
    "IMPORTED_SEMANTIC",     # extracted from the owner's prose (still theirs)
    "COMMITTED_EVENT",       # something that actually happened in play
    "AI_PROPOSED_CANON",     # AI-proposed, owner-approved world
    "AI_INFERRED_CONNECTOR", # AI-added connective geography (streets/doors)
    "AI_RUNTIME_EXPANDED",   # AI-added ordinary place during play
)
PRIORITY: dict[str, int] = {
    name: len(PROVENANCE_LADDER) - i for i, name in enumerate(PROVENANCE_LADDER)
}

# Provenances that represent EXPLICIT owner intent — never overwritable by AI, and
# a conflict between two of them is a contradiction to surface, not auto-resolve.
OWNER_EXPLICIT = frozenset({"IMPORTED_EXPLICIT", "OWNER_EDITED"})

# Legacy aliases the codebase already emits, mapped onto the ladder.
_ALIASES = {
    "IMPORTED": "IMPORTED_EXPLICIT",
    "IMPORTED_CANON": "IMPORTED_EXPLICIT",
    "AUTHORED": "IMPORTED_EXPLICIT",
    "EXPLICITLY_AUTHORED": "IMPORTED_EXPLICIT",
    "AI_NORMALIZED": "IMPORTED_SEMANTIC",
    "AI_EXPANDED": "AI_RUNTIME_EXPANDED",
    "AI_PROPOSED": "AI_PROPOSED_CANON",
}


def canonical(provenance: str) -> str:
    p = (provenance or "").upper()
    return _ALIASES.get(p, p)


def priority(provenance: str) -> int:
    return PRIORITY.get(canonical(provenance), 0)


def is_owner_explicit(provenance: str) -> bool:
    return canonical(provenance) in OWNER_EXPLICIT


def outranks(a: str, b: str) -> bool:
    """True if provenance `a` strictly outranks `b` (so a fact with `a` may
    overwrite one with `b`)."""
    return priority(a) > priority(b)


def may_overwrite(existing: str, incoming: str) -> bool:
    """Whether an `incoming`-provenance fact may overwrite an `existing` one.

    Owner-explicit canon is NEVER overwritten by AI content. Two owner-explicit
    facts about the same thing must NOT be silently resolved — the caller gets
    False and should surface the contradiction for review. Otherwise the stronger
    (or equal, treated as an update) provenance wins."""
    if is_owner_explicit(existing) and not is_owner_explicit(incoming):
        return False
    if is_owner_explicit(existing) and is_owner_explicit(incoming):
        return False  # contradiction between two owner facts — surface, don't resolve
    return priority(incoming) >= priority(existing)
