"""Scene entity identity — the authorized directory of who/what is present.

The one domain truth for "the present cast": distinct PLAYER_CHARACTERs (each with
its controlling member), NPCs, and (later) creatures/objects. Task-specific views
are rendered from this; nothing invents a second definition of "the party".

Presence is NOT party membership (see docs/multiplayer-identity.md): a party member
who split off is known-but-absent, not a reachable target.
"""
from app.entities.directory import (
    NPC_TYPE,
    PLAYER_CHARACTER,
    EntityContext,
    SceneDirectory,
    SceneEntityDirectory,
    TargetResolution,
)

__all__ = [
    "EntityContext",
    "NPC_TYPE",
    "PLAYER_CHARACTER",
    "SceneDirectory",
    "SceneEntityDirectory",
    "TargetResolution",
]
