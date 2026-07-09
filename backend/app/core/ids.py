"""ID generation and entity-reference helpers.

Primary keys are 32-char hex UUID4 strings — dialect-portable across SQLite (tests)
and PostgreSQL (production).

Entity references used in the event log look like ``character:<id>``,
``npc:<id>``, ``location:<id>``, or the literal ``system``.
"""
from __future__ import annotations

from uuid import uuid4


def new_id() -> str:
    return uuid4().hex


def entity_ref(kind: str, entity_id: str) -> str:
    return f"{kind}:{entity_id}"


SYSTEM_ACTOR = "system"


def parse_entity_ref(ref: str) -> tuple[str, str | None]:
    """Return (kind, id). ``system`` parses to ("system", None)."""
    if ref == SYSTEM_ACTOR:
        return SYSTEM_ACTOR, None
    kind, _, entity_id = ref.partition(":")
    return kind, (entity_id or None)
