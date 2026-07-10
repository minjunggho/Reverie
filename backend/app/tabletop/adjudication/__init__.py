"""Adjudication: the deterministic side of turning an AI decision into a resolution.

The AI PROPOSES (resolution type, ability/skill, DC band). The code here OWNS the
numbers: it computes the character's modifier, clamps the DC to an allowed band, and
decides advantage/disadvantage. The AI never supplies a modifier or a DC integer.
"""
from app.tabletop.adjudication.resolver import (
    check_modifier,
    decide_clarification,
    normalize_ability,
    normalize_skill,
    resolve_dc,
)
from app.tabletop.adjudication.deltas import ALLOWED_DELTA_KINDS, DeltaApplier

__all__ = [
    "resolve_dc",
    "check_modifier",
    "decide_clarification",
    "normalize_ability",
    "normalize_skill",
    "DeltaApplier",
    "ALLOWED_DELTA_KINDS",
]
