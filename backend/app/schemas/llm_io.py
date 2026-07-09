"""Structured schemas for every LLM job I/O.

Guiding rule: the LLM returns *typed proposals and prose*, never raw database
mutations and never numbers it is forbidden to own (dice, modifiers, HP totals).
The engine converts validated proposals into state changes.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.enums import (
    ConsequenceClass,
    DifficultyBand,
    MessageCategory,
    ResolutionType,
)


# --- Classification (non-`!` messages) --------------------------------------
class ClassificationResult(BaseModel):
    category: MessageCategory
    confidence: float = Field(ge=0.0, le=1.0)
    # Optional short DM reply for questions / dialogue. Never a state mutation.
    suggested_response: Optional[str] = None


# --- Action interpretation (`!` messages) -----------------------------------
class ActionInterpretation(BaseModel):
    goal: str
    method: str
    target_references: list[str] = Field(default_factory=list)
    declared_constraints: list[str] = Field(default_factory=list)
    risk_awareness: list[str] = Field(default_factory=list)
    intent_confidence: float = Field(ge=0.0, le=1.0)
    missing_information: list[str] = Field(default_factory=list)


# --- Adjudication ------------------------------------------------------------
class AdjudicationDecision(BaseModel):
    needs_clarification: bool = False
    clarification_question: Optional[str] = None  # Thai, one focused question

    resolution_type: ResolutionType = ResolutionType.ABILITY_CHECK
    ability: Optional[str] = None   # "str"|"dex"|"con"|"int"|"wis"|"cha"
    skill: Optional[str] = None     # e.g. "stealth", "perception"
    dc_band: Optional[DifficultyBand] = None
    advantage: bool = False
    disadvantage: bool = False
    # Opposed checks name the opponent whose passive score the engine reads.
    contested_against: Optional[str] = None
    rationale: str = ""


# --- Clarification model -----------------------------------------------------
class ClarificationResult(BaseModel):
    """The engine's decision (not the LLM's) about whether to pause and ask."""
    needs_clarification: bool
    question: Optional[str] = None  # Thai, one focused question
    reason: str = ""


# --- Consequence proposal ----------------------------------------------------
class ProposedDelta(BaseModel):
    """A single proposed canonical change. The engine validates `kind` against an
    allowlist and checks authority before committing; unknown kinds are rejected.
    """
    kind: str                       # e.g. "advance_time", "raise_suspicion", "hp_change"
    target: Optional[str] = None    # entity ref, e.g. "npc:<id>"
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class ConsequenceProposal(BaseModel):
    consequence_class: ConsequenceClass
    deltas: list[ProposedDelta] = Field(default_factory=list)
    narration_hint: str = ""


# --- Narration ---------------------------------------------------------------
class Narration(BaseModel):
    text: str
    style: str = "concise"  # "concise" | "cinematic"


# --- Recap -------------------------------------------------------------------
class Recap(BaseModel):
    text: str


# --- NPC response (Phase 11) -------------------------------------------------
class ProposedBeliefDelta(BaseModel):
    npc_id: str
    subject: str
    new_status: str          # KnowledgeStatus value
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = ""


class NPCResponse(BaseModel):
    utterance: str
    proposed_belief_deltas: list[ProposedBeliefDelta] = Field(default_factory=list)
    proposed_attitude: Optional[str] = None


# --- Post-session (Phase 10) -------------------------------------------------
class PostSessionReport(BaseModel):
    player_summary: str
    continuity_report: dict[str, Any] = Field(default_factory=dict)
