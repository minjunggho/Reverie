"""AI jobs: single-responsibility tasks with strict contracts (§29).

Each job builds a task-specific, visibility-enforced context, calls the provider,
validates the structured output, and has a safe fallback on model failure. Jobs
never write to the database and never produce authoritative numbers.
"""
from app.ai.jobs.adjudicator import AdjudicationJudge
from app.ai.jobs.classifier import TableMessageClassifier
from app.ai.jobs.consequence import ConsequencePlanner
from app.ai.jobs.interpreter import ActionInterpreter
from app.ai.jobs.narrator import DMNarrator
from app.ai.jobs.npc_response import NPCResponseGenerator
from app.ai.jobs.recap import SafeRecapGenerator

__all__ = [
    "TableMessageClassifier",
    "ActionInterpreter",
    "AdjudicationJudge",
    "ConsequencePlanner",
    "DMNarrator",
    "NPCResponseGenerator",
    "SafeRecapGenerator",
]
