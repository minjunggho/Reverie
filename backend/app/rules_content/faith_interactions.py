"""Versioned doctrine metadata layered onto the existing faith registry."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.rules_content.faith_registry import FaithContentError, FaithRegistry, get_faith_registry

_ROOT = Path(__file__).parent / "pantheons"


class DoctrineDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    deity_key: str
    values: tuple[str, ...] = ()
    supported_event_tags: tuple[str, ...] = ()
    opposed_event_tags: tuple[str, ...] = ()
    source: str = Field(min_length=1)


class FaithInteractionRegistry:
    def __init__(self, doctrines: tuple[DoctrineDefinition, ...], faith: FaithRegistry) -> None:
        self._doctrines = {item.deity_key: item for item in doctrines}
        for item in doctrines:
            if faith.get_deity(item.deity_key) is None:
                raise FaithContentError(
                    f"faith interaction doctrine references unknown deity {item.deity_key!r}"
                )

    def doctrine(self, deity_key: str) -> DoctrineDefinition | None:
        return self._doctrines.get(deity_key)


@lru_cache(maxsize=1)
def get_faith_interaction_registry() -> FaithInteractionRegistry:
    rows: list[DoctrineDefinition] = []
    for path in sorted(_ROOT.glob("*/interactions.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for item in raw.get("doctrines", []):
            rows.append(DoctrineDefinition.model_validate(item))
    keys = [item.deity_key for item in rows]
    if len(keys) != len(set(keys)):
        raise FaithContentError("duplicate deity doctrine interaction key")
    return FaithInteractionRegistry(tuple(rows), get_faith_registry())


__all__ = ["DoctrineDefinition", "FaithInteractionRegistry", "get_faith_interaction_registry"]
