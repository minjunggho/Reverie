"""Deterministic DC + modifier resolution, and the clarification decision.

- `resolve_dc` clamps an AI-proposed band to the allowed DC ladder (default Medium 15).
- `check_modifier` computes the character's total modifier from authoritative state.
- `decide_clarification` is the ENGINE's decision (not the LLM's) about pausing to ask.
"""
from __future__ import annotations

from app.models.character import Character
from app.models.enums import BAND_TO_DC, DifficultyBand
from app.schemas.llm_io import (
    ActionInterpretation,
    AdjudicationDecision,
    ClarificationResult,
)
from app.tabletop.rules import ability_modifier

# Below this interpretation confidence, we ask rather than guess.
CLARIFY_CONFIDENCE_THRESHOLD = 0.55


def resolve_dc(band: DifficultyBand | None) -> int:
    if band is None:
        return BAND_TO_DC[DifficultyBand.MEDIUM]
    return BAND_TO_DC[band]


def check_modifier(character: Character, ability: str, skill: str | None) -> tuple[int, bool]:
    """Return (total_modifier, proficient). Proficiency applies only when the chosen
    skill is one the character is proficient in."""
    mod = ability_modifier(character.ability_score(ability))
    proficient = bool(skill and skill in (character.proficiencies or []))
    if proficient:
        mod += character.proficiency_bonus
    return mod, proficient


def decide_clarification(
    interpretation: ActionInterpretation, decision: AdjudicationDecision
) -> ClarificationResult:
    """Clarify only when the missing info meaningfully changes target/method/
    resolution/risk/consequence/interpretation. Prefer assume-and-state otherwise."""
    if decision.needs_clarification:
        return ClarificationResult(
            needs_clarification=True,
            question=decision.clarification_question,
            reason="adjudicator flagged ambiguity affecting resolution",
        )
    if interpretation.missing_information and interpretation.intent_confidence < CLARIFY_CONFIDENCE_THRESHOLD:
        return ClarificationResult(
            needs_clarification=True,
            question=None,  # pipeline will phrase from missing_information
            reason="interpretation confidence too low with material missing info",
        )
    return ClarificationResult(needs_clarification=False)
