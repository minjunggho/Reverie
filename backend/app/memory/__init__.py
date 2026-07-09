"""Task-specific context builders.

We NEVER send a full transcript or whole campaign history to the model. Each builder
retrieves only what its task needs and enforces information visibility at retrieval
time (§19/§20), so a player-facing builder physically cannot receive DM-only facts.
"""
from app.memory.context_builders import (
    build_action_interpretation_context,
    build_adjudication_context,
    build_classification_context,
    build_consequence_context,
    build_narration_context,
    build_npc_response_context,
    build_recap_context,
    scene_brief,
)

__all__ = [
    "scene_brief",
    "build_classification_context",
    "build_action_interpretation_context",
    "build_adjudication_context",
    "build_consequence_context",
    "build_narration_context",
    "build_npc_response_context",
    "build_recap_context",
]
