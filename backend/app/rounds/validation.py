"""Deterministic pre-lock validation of a single submission.

Minor problems are returned to the OWNING player (NEEDS_CORRECTION) without discarding
anyone else's submission — the ready-gate simply won't count an invalid one. This is a
rules check, not a narration: it never rolls, never commits, and never invents a target.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.entities import SceneEntityDirectory
from app.models.character import Character
from app.models.decision_window import ActionSubmission, DecisionWindow
from app.models.enums import SubmissionValidation

_INCAP_CONDITIONS = {"unconscious", "หมดสติ", "incapacitated", "อัมพาต", "petrified", "stunned", "มึนงง"}


@dataclass
class ValidationResult:
    status: str
    errors: list[str]

    @property
    def ok(self) -> bool:
        return self.status == SubmissionValidation.VALID.value


async def validate_submission(
    session: AsyncSession, *, window: DecisionWindow, submission: ActionSubmission,
    scene, directory: SceneEntityDirectory | None = None, campaign_id: str,
) -> ValidationResult:
    """Check eligibility, target presence, and declared-resource sanity. Returns a
    result AND writes it onto the submission so the panel and the resolver agree."""
    errors: list[str] = []

    # 1) Actor eligibility — must be a required actor and not incapacitated.
    if submission.actor_id not in (window.required_actor_ids or []):
        errors.append("ตัวละครนี้ไม่ได้อยู่ในรอบนี้")
    actor = await session.get(Character, submission.actor_id)
    if actor is not None:
        if actor.dead or actor.hp <= 0:
            errors.append("ตัวละครหมดสติ/ล้มแล้ว — ทำแอ็กชันปกติไม่ได้ในรอบนี้")
        elif set(c.lower() for c in (actor.conditions or [])) & _INCAP_CONDITIONS:
            errors.append("ตัวละครติดสภาวะที่ทำให้ลงมือไม่ได้")

    # 2) Target presence/visibility — a named target must be someone/something in scene.
    if directory is not None:
        for label, ref in (("เป้าหมาย", submission.action_target),
                            ("เป้าหมายสำรอง", submission.fallback_target),
                            ("เป้าหมายโบนัส", submission.bonus_target)):
            if not ref:
                continue
            resolution = directory.resolve_mentions([ref])
            if resolution.not_present and not resolution.resolved:
                errors.append(f"{label} “{ref}” ไม่ได้อยู่ในฉากนี้")
            elif not resolution.resolved and not resolution.ambiguous:
                errors.append(f"{label} “{ref}” หาไม่พบในฉาก")

    # 3) Declared resources must at least be nameable (deep availability is checked by
    # the resolving engine; here we only catch an empty/garbage declaration early).
    for res in (submission.declared_resource_use or []):
        if not str(res).strip():
            errors.append("ประกาศใช้ทรัพยากรว่างเปล่า")

    status = (SubmissionValidation.VALID.value if not errors
              else SubmissionValidation.NEEDS_CORRECTION.value)
    submission.validation_status = status
    submission.validation_errors = errors
    return ValidationResult(status=status, errors=errors)
