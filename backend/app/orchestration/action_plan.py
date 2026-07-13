"""ActionPlan construction + per-step interpretation synthesis.

The interpreter (LLM) owns splitting compound input into ordered `ActionStep`s and
classifying each as IMMEDIATE / FUTURE / FLAVOR — the engine never does NLP. This
module turns that typed output into an executable `ActionPlan` and, for the pipeline
executor, synthesizes a focused single-action `ActionInterpretation` per step so
each step flows through the SAME routing every simple action already uses (no
parallel execution path).
"""
from __future__ import annotations

from app.schemas.llm_io import ActionInterpretation, ActionPlan, ActionStep


def build_plan(interpretation: ActionInterpretation, *, actor_ref: str = "") -> ActionPlan:
    """An ordered plan from the interpretation's steps. Empty steps → a single
    OTHER step derived from the flat interpretation, so callers can treat every
    action uniformly; the pipeline only takes the ordered path when ≥2 IMMEDIATE
    steps exist (otherwise the existing flat-flag routing runs, unchanged)."""
    steps = list(interpretation.steps)
    if not steps:
        steps = [_derive_single_step(interpretation)]
    return ActionPlan(
        actor_ref=actor_ref, steps=steps,
        confidence=interpretation.intent_confidence,
        ambiguity="; ".join(interpretation.missing_information),
    )


def _derive_single_step(interp: ActionInterpretation) -> ActionStep:
    if interp.cast_intent:
        kind = "CAST"
    elif interp.movement_intent:
        kind = "MOVE"
    elif interp.social_intent:
        kind = "SPEAK"
    else:
        kind = "OTHER"
    return ActionStep(
        kind=kind, text=interp.goal, targets=list(interp.target_references),
        method=interp.method, destination=interp.movement_reference,
        spell_reference=interp.spell_reference, temporal="IMMEDIATE",
    )


def step_to_interpretation(step: ActionStep) -> ActionInterpretation:
    """Synthesize a focused single-action interpretation for ONE step, setting only
    the flag its kind implies. This reuses every existing domain handler (social,
    travel, cast, adjudication) — the ordered executor adds sequencing, not new
    mechanics."""
    interp = ActionInterpretation(
        goal=step.text or step.method or step.kind,
        method=step.method or step.text,
        target_references=list(step.targets),
        intent_confidence=1.0,
    )
    if step.kind == "MOVE":
        interp.movement_intent = True
        interp.movement_kind = "RETURN_OR_EXIT" if _is_exit(step) else "CANONICAL_TRAVEL"
        interp.movement_reference = step.destination or step.text
    elif step.kind == "CAST":
        interp.cast_intent = True
        interp.spell_reference = step.spell_reference or step.text
    elif step.kind == "SPEAK":
        interp.social_intent = True
    # ATTACK / INTERACT / SEARCH / HIDE / USE_ITEM / TRANSFER_* / WAIT / OTHER flow
    # through the ordinary adjudication path (an ability check or auto-resolution).
    return interp


def _is_exit(step: ActionStep) -> bool:
    blob = f"{step.destination} {step.text}".lower()
    return any(w in blob for w in ("ออก", "ข้างนอก", "exit", "outside", "leave", "หนี", "วิ่งหนี"))
