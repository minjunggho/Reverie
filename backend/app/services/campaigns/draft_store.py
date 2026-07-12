"""Draft persistence — every write is a database-level compare-and-update.

The creation flow holds a per-(campaign, member) asyncio lock, but that only
serializes ONE process. A multi-process deployment (or a double-delivered
Discord interaction landing on two workers) could still interleave read-modify-
write cycles on `draft.data`. These helpers make the row itself the arbiter:
each save asserts the `version` it read; a stale writer gets `DraftConflict`
instead of silently overwriting the other writer's selections, and the caller
re-renders from the persisted state.
"""
from __future__ import annotations

from sqlalchemy import update

from app.core.errors import ReverieError
from app.models.character_draft import CharacterDraft


class DraftConflict(ReverieError):
    """The draft changed underneath this writer; re-read and re-render."""


async def save_draft(db, draft: CharacterDraft, data: dict, *, step_inc: int = 0) -> None:
    """Persist `data` iff the draft is still ACTIVE at the version we read.

    On success the in-memory `draft` object is advanced so a handler that saves
    more than once in a single turn keeps a valid expectation.
    """
    expected = int(getattr(draft, "version", 0) or 0)
    async with db.unit_of_work() as s:
        result = await s.execute(
            update(CharacterDraft)
            .where(
                CharacterDraft.id == draft.id,
                CharacterDraft.version == expected,
                CharacterDraft.status == "ACTIVE",
            )
            .values(
                data=data,
                version=expected + 1,
                step=CharacterDraft.step + step_inc,
            )
        )
        if result.rowcount != 1:
            raise DraftConflict(
                f"draft {draft.id} changed concurrently (expected version {expected})"
            )
    draft.version = expected + 1
    draft.step = int(getattr(draft, "step", 0) or 0) + step_inc


async def close_draft(db, draft_id: str, *, status: str) -> bool:
    """ACTIVE → DONE/CANCELLED, exactly once. Returns False if already closed."""
    async with db.unit_of_work() as s:
        result = await s.execute(
            update(CharacterDraft)
            .where(CharacterDraft.id == draft_id, CharacterDraft.status == "ACTIVE")
            .values(status=status)
        )
        return result.rowcount == 1
