"""Optimistic-concurrency helpers.

The serialized per-session queue (Phase 4) already prevents most races by processing
committed actions one at a time. `version` columns are the second line of defence: a
guarded update only succeeds if the row is still at the version the writer read, so a
stale writer loses instead of silently clobbering fresher state.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError


async def guarded_version_update(
    session: AsyncSession,
    model: type[Any],
    entity_id: str,
    expected_version: int,
    **changes: Any,
) -> int:
    """UPDATE row SET ...changes, version=expected+1 WHERE id AND version=expected.

    Returns the new version. Raises ConflictError if the row moved on (rowcount 0).
    """
    new_version = expected_version + 1
    result = await session.execute(
        update(model)
        .where(model.id == entity_id, model.version == expected_version)
        .values(version=new_version, **changes)
    )
    if result.rowcount != 1:
        raise ConflictError(
            f"optimistic lock failed on {model.__name__} {entity_id} "
            f"(expected version {expected_version})"
        )
    return new_version
