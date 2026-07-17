"""Adjudication: the deterministic side of turning an AI decision into a resolution.

The AI PROPOSES (resolution type, ability/skill, DC band, and the NAMES of situational
factors). The code here OWNS the numbers: it computes the character's modifier,
composes the DC from the band plus capped situational factors — half of them derived
from authoritative state, none of them priced by the model — and decides
advantage/disadvantage. The AI never supplies a modifier or a DC integer.
"""
from app.tabletop.adjudication.resolver import (
    check_modifier,
    decide_clarification,
    normalize_ability,
    normalize_skill,
    resolve_dc,
)
from app.tabletop.adjudication.difficulty import (
    DC_CEILING,
    DC_FLOOR,
    MAX_TOTAL_SWING,
    SITUATIONAL_FACTORS,
    ComposedDC,
    DCFactor,
    FactorDef,
    compose_dc,
    factors_from_keys,
)
from app.tabletop.adjudication.situation import SituationReader
from app.tabletop.adjudication.deltas import ALLOWED_DELTA_KINDS, DeltaApplier

__all__ = [
    "resolve_dc",
    "check_modifier",
    "decide_clarification",
    "normalize_ability",
    "normalize_skill",
    "compose_dc",
    "factors_from_keys",
    "ComposedDC",
    "DCFactor",
    "FactorDef",
    "SITUATIONAL_FACTORS",
    "SituationReader",
    "MAX_TOTAL_SWING",
    "DC_FLOOR",
    "DC_CEILING",
    "DeltaApplier",
    "ALLOWED_DELTA_KINDS",
]
