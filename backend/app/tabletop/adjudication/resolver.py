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
from app.tabletop.rules import ABILITIES, SUPPORTED_SKILLS, ability_modifier

# Below this interpretation confidence, we ask rather than guess.
CLARIFY_CONFIDENCE_THRESHOLD = 0.55

# Real LLMs return ability names in many forms ("Dexterity", "dexterity", "DEX").
# Normalize to our six codes instead of rejecting them.
_ABILITY_ALIASES = {
    "strength": "str", "str": "str",
    "dexterity": "dex", "dex": "dex",
    "constitution": "con", "con": "con",
    "intelligence": "int", "int": "int",
    "wisdom": "wis", "wis": "wis",
    "charisma": "cha", "cha": "cha",
}


def normalize_ability(name: str | None) -> str | None:
    """Return a valid ability code, or None if it can't be mapped."""
    if not name:
        return None
    k = name.strip().lower()
    k = _ABILITY_ALIASES.get(k, k)
    return k if k in ABILITIES else None


def normalize_skill(name: str | None) -> str | None:
    """Return a supported skill (snake_case), or None if unsupported."""
    if not name:
        return None
    k = name.strip().lower().replace(" ", "_").replace("-", "_")
    return k if k in SUPPORTED_SKILLS else None


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
    resolution/risk/consequence/interpretation. Prefer assume-and-state otherwise.

    ENGINE GATE (playtest fix): an adjudicator's clarification request is honored
    only when the interpreter ALSO found material missing information or read the
    intent with low confidence. "! แอบฟังต่อไป" in a scene with one established
    conversation is a complete intent — an eager "ฟังเรื่องอะไร?" gets suppressed
    here regardless of what the model proposed."""
    interpreter_unsure = bool(interpretation.missing_information) or (
        interpretation.intent_confidence < CLARIFY_CONFIDENCE_THRESHOLD
    )
    if decision.needs_clarification and interpreter_unsure:
        return ClarificationResult(
            needs_clarification=True,
            question=decision.clarification_question,
            reason="ambiguity affects resolution (both judges unsure)",
        )
    if interpretation.missing_information and interpretation.intent_confidence < CLARIFY_CONFIDENCE_THRESHOLD:
        return ClarificationResult(
            needs_clarification=True,
            question=None,  # pipeline will phrase from missing_information
            reason="interpretation confidence too low with material missing info",
        )
    return ClarificationResult(needs_clarification=False)
