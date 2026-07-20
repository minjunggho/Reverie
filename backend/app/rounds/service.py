"""DecisionWindowService — the server-authoritative shared-planning state machine.

Every decision is computed from persisted rows, never from chat-message order or client
state: readiness, freezing, and resolution eligibility are read from the DB under
optimistic concurrency (`DecisionWindow.version`, `ActionSubmission.revision`). Submit
and edit are an idempotent upsert of one row per actor; editing bumps the revision and
clears Ready; Ready locks a specific revision so a stale client cannot ready an intention
the player has since changed. Freezing writes the immutable snapshot exactly once.
"""
from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.errors import ConflictError, NotFoundError, RulesViolation
from app.models.decision_window import ActionSubmission, DecisionWindow
from app.models.enums import (
    SubmissionValidation,
    SubmissionVisibility,
    WindowMode,
    WindowPhase,
)
from app.rounds.parser import parse_submission
from app.rounds.policies import WindowPolicies

# The structured columns a submission accepts from the UI or the parser.
_SETTABLE = (
    "dialogue", "movement_intent", "destination", "primary_action", "action_target",
    "bonus_action", "bonus_target", "interaction", "reaction_intent", "condition",
    "fallback_action", "fallback_target", "desired_tone",
)
_OPEN = WindowPhase.AWAITING_ACTIONS.value


class DecisionWindowService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- lifecycle -----------------------------------------------------------
    async def open_window(
        self, *, campaign_id: str, session_id: str, scene_id: str | None,
        round_id: int = 1, mode: WindowMode = WindowMode.NONCOMBAT,
        required_actor_ids: Iterable[str], policies: WindowPolicies | None = None,
    ) -> DecisionWindow:
        """Open (or return the existing) window for this scene+round. Idempotent so a
        retry or a second player arriving never creates a duplicate round."""
        existing = await self._by_scene_round(scene_id, round_id)
        if existing is not None:
            return existing
        window = DecisionWindow(
            campaign_id=campaign_id, session_id=session_id, scene_id=scene_id,
            round_id=round_id, mode=mode.value, phase=_OPEN,
            required_actor_ids=list(dict.fromkeys(required_actor_ids)),
            excused_actor_ids=[],
            config=(policies or WindowPolicies()).as_snapshot(),
        )
        self.session.add(window)
        await self.session.flush()
        return window

    async def get(self, window_id: str) -> DecisionWindow:
        window = await self.session.get(DecisionWindow, window_id)
        if window is None:
            raise NotFoundError(f"decision window {window_id} not found")
        return window

    # --- submissions (AWAITING_ACTIONS only) ---------------------------------
    async def submit(
        self, *, window_id: str, actor_id: str, raw_text: str = "",
        fields: dict[str, Any] | None = None, visibility: str | None = None,
        idempotency_key: str | None = None, expected_revision: int | None = None,
    ) -> ActionSubmission:
        """Create or replace this actor's intention. Editing bumps the revision and
        clears Ready. A duplicate call carrying the same `idempotency_key` is a no-op."""
        window = await self.get(window_id)
        self._require_open(window)
        sub = await self._submission(window_id, actor_id)

        if sub is not None and idempotency_key and sub.idempotency_key == idempotency_key:
            return sub                                  # idempotent replay: no-op

        if sub is None:
            sub = ActionSubmission(window_id=window_id, actor_id=actor_id, revision=1)
            self.session.add(sub)
        else:
            # A stale editor (two clients on the same revision) is rejected, not merged.
            if expected_revision is not None and expected_revision != sub.revision:
                raise ConflictError(
                    f"submission revision {expected_revision} is stale "
                    f"(current {sub.revision})")
            sub.revision += 1
            sub.ready_at = None                         # editing ALWAYS unsets Ready
            sub.passed = False

        data = {**parse_submission(raw_text), **(fields or {})} if (raw_text or fields) else {}
        sub.raw_player_text = raw_text or sub.raw_player_text
        for key in _SETTABLE:
            if key in data:
                setattr(sub, key, data[key])
        if visibility is not None:
            sub.visibility = visibility
        sub.validation_status = SubmissionValidation.PENDING.value
        sub.validation_errors = []
        sub.idempotency_key = idempotency_key
        await self.session.flush()

        # Solo fast-path: one required actor + policy → submitting IS being ready.
        if WindowPolicies.from_config({"planning": window.config}).solo_auto_ready(
            len(self._waiting_on(window))
        ):
            sub.ready_at = utcnow()
            await self.session.flush()
        return sub

    async def delete_submission(self, *, window_id: str, actor_id: str) -> None:
        window = await self.get(window_id)
        self._require_open(window)
        sub = await self._submission(window_id, actor_id)
        if sub is not None:
            await self.session.delete(sub)
            await self.session.flush()

    # --- readiness -----------------------------------------------------------
    async def mark_ready(
        self, *, window_id: str, actor_id: str, revision: int,
    ) -> ActionSubmission:
        """Ready locks a specific revision. If the player has edited since (revision
        moved on), the stale Ready is rejected — the client must re-read and re-ready."""
        window = await self.get(window_id)
        self._require_open(window)
        sub = await self._submission(window_id, actor_id)
        if sub is None:
            raise RulesViolation("cannot ready before submitting an action")
        if revision != sub.revision:
            raise ConflictError(
                f"ready targets revision {revision} but current is {sub.revision}")
        sub.ready_at = sub.ready_at or utcnow()          # duplicate Ready is a no-op
        await self.session.flush()
        return sub

    async def unmark_ready(self, *, window_id: str, actor_id: str) -> None:
        sub = await self._submission(window_id, actor_id)
        if sub is not None:
            sub.ready_at = None
            sub.passed = False
            await self.session.flush()

    async def pass_turn(self, *, window_id: str, actor_id: str) -> ActionSubmission:
        """An explicit choice not to act — counts as ready, resolves to nothing."""
        window = await self.get(window_id)
        self._require_open(window)
        sub = await self._submission(window_id, actor_id)
        if sub is None:
            sub = ActionSubmission(window_id=window_id, actor_id=actor_id, revision=1)
            self.session.add(sub)
        sub.passed = True
        sub.ready_at = utcnow()
        await self.session.flush()
        return sub

    async def excuse_actor(self, *, window_id: str, actor_id: str) -> DecisionWindow:
        """Host/host-policy: exclude a disconnected/AFK actor from THIS round's ready
        gate. Their pending submission (if any) is kept but no longer blocks resolution."""
        window = await self.get(window_id)
        excused = set(window.excused_actor_ids or [])
        excused.add(actor_id)
        window.excused_actor_ids = sorted(excused)
        window.version += 1
        await self.session.flush()
        return window

    # --- gating + freeze -----------------------------------------------------
    async def all_required_ready(self, window: DecisionWindow) -> bool:
        waiting = self._waiting_on(window)
        if not waiting:
            return True
        subs = {s.actor_id: s for s in await self.submissions(window.id)}
        return all(subs.get(a) is not None and subs[a].is_ready for a in waiting)

    async def freeze(self, window: DecisionWindow, *, forced: bool = False) -> dict:
        """Freeze the current submissions into the immutable snapshot exactly once.
        Idempotent: a second call returns the same snapshot (duplicate-resolve guard)."""
        if window.frozen_snapshot is not None:
            return window.frozen_snapshot
        if not forced and not await self.all_required_ready(window):
            raise RulesViolation("cannot freeze: not all required players are ready")
        subs = await self.submissions(window.id)
        snapshot = {
            "window_id": window.id, "round_id": window.round_id, "mode": window.mode,
            "frozen_at": utcnow().isoformat(), "forced": forced,
            "required_actor_ids": list(window.required_actor_ids or []),
            "excused_actor_ids": list(window.excused_actor_ids or []),
            "submissions": [self.serialize(s) for s in subs],
        }
        window.frozen_snapshot = snapshot
        window.phase = WindowPhase.READY_TO_RESOLVE.value
        window.version += 1
        await self.session.flush()
        return snapshot

    async def force_resolve(self, window: DecisionWindow) -> dict:
        """Host override: freeze now regardless of who is ready."""
        return await self.freeze(window, forced=True)

    async def reopen(self, window: DecisionWindow) -> DecisionWindow:
        """Host: return a frozen-but-unresolved window to planning. A round that already
        produced a world update cannot be reopened (that would double-apply state)."""
        if window.resolved:
            raise RulesViolation("round already resolved; open a new window instead")
        window.frozen_snapshot = None
        window.phase = _OPEN
        window.version += 1
        for sub in await self.submissions(window.id):
            sub.ready_at = None
            sub.passed = False
        await self.session.flush()
        return window

    async def cancel(self, window: DecisionWindow) -> DecisionWindow:
        window.phase = WindowPhase.CANCELLED.value
        window.version += 1
        await self.session.flush()
        return window

    # --- reads ---------------------------------------------------------------
    async def submissions(self, window_id: str) -> list[ActionSubmission]:
        rows = await self.session.execute(
            select(ActionSubmission).where(ActionSubmission.window_id == window_id)
            .order_by(ActionSubmission.created_at))
        return list(rows.scalars())

    async def panel(self, window: DecisionWindow, *, viewer_id: str | None = None) -> dict:
        """The planning-panel view. A SECRET submission's contents are hidden from other
        players (only its owner and the host-view see them); its mere existence/readiness
        is still shown so the table knows who has acted."""
        subs = await self.submissions(window.id)
        waiting = self._waiting_on(window)
        cards = []
        for s in subs:
            hidden = (s.visibility == SubmissionVisibility.SECRET.value
                      and viewer_id is not None and viewer_id != s.actor_id)
            cards.append({
                "actor_id": s.actor_id,
                "status": self._status(s),
                "ready": s.is_ready,
                "revision": s.revision,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                "secret": s.visibility == SubmissionVisibility.SECRET.value,
                "preview": None if hidden else (s.raw_player_text or s.primary_action),
                "validation": s.validation_status,
            })
        submitted = {s.actor_id for s in subs}
        return {
            "window_id": window.id, "phase": window.phase, "mode": window.mode,
            "round_id": window.round_id,
            "waiting_on": [a for a in waiting if a not in submitted
                           or not next((s for s in subs if s.actor_id == a)).is_ready],
            "not_submitted": [a for a in waiting if a not in submitted],
            "excused": list(window.excused_actor_ids or []),
            "cards": cards,
        }

    @staticmethod
    def serialize(sub: ActionSubmission) -> dict:
        return {
            "actor_id": sub.actor_id, "revision": sub.revision,
            "raw_player_text": sub.raw_player_text, "dialogue": sub.dialogue,
            "movement_intent": sub.movement_intent, "destination": sub.destination,
            "primary_action": sub.primary_action, "action_target": sub.action_target,
            "bonus_action": sub.bonus_action, "bonus_target": sub.bonus_target,
            "interaction": sub.interaction, "reaction_intent": sub.reaction_intent,
            "condition": sub.condition, "fallback_action": sub.fallback_action,
            "fallback_target": sub.fallback_target, "desired_tone": sub.desired_tone,
            "declared_resource_use": list(sub.declared_resource_use or []),
            "required_rolls": list(sub.required_rolls or []),
            "visibility": sub.visibility, "passed": sub.passed,
            "validation_status": sub.validation_status,
            "validation_errors": list(sub.validation_errors or []),
        }

    # --- internals -----------------------------------------------------------
    def _waiting_on(self, window: DecisionWindow) -> list[str]:
        excused = set(window.excused_actor_ids or [])
        return [a for a in (window.required_actor_ids or []) if a not in excused]

    @staticmethod
    def _status(sub: ActionSubmission) -> str:
        if sub.passed:
            return "passed"
        if sub.validation_status == SubmissionValidation.NEEDS_CORRECTION.value:
            return "needs_correction"
        if sub.is_ready:
            return "ready"
        if sub.raw_player_text or sub.primary_action:
            return "submitted"
        return "choosing"

    @staticmethod
    def _require_open(window: DecisionWindow) -> None:
        if window.phase != _OPEN:
            raise RulesViolation(
                f"window is {window.phase}; submissions are locked")

    async def _submission(self, window_id: str, actor_id: str) -> ActionSubmission | None:
        rows = await self.session.execute(
            select(ActionSubmission).where(
                ActionSubmission.window_id == window_id,
                ActionSubmission.actor_id == actor_id))
        return rows.scalars().first()

    async def _by_scene_round(self, scene_id: str | None, round_id: int) -> DecisionWindow | None:
        if scene_id is None:
            return None
        rows = await self.session.execute(
            select(DecisionWindow).where(
                DecisionWindow.scene_id == scene_id,
                DecisionWindow.round_id == round_id))
        return rows.scalars().first()
