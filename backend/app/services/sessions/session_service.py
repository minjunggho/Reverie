"""Session lifecycle service with validated transitions."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.errors import IllegalStateTransition, NotFoundError
from app.models.enums import ActivePlayState, SessionStatus
from app.models.session import Session

# Allowed session-status transitions (practical lifecycle, see state-machine doc).
_SESSION_TRANSITIONS: dict[SessionStatus, set[SessionStatus]] = {
    SessionStatus.PREPARATION: {SessionStatus.OPENING, SessionStatus.ACTIVE_PLAY},
    SessionStatus.OPENING: {SessionStatus.ACTIVE_PLAY},
    SessionStatus.ACTIVE_PLAY: {SessionStatus.CLOSING},
    SessionStatus.CLOSING: {SessionStatus.POST_SESSION},
    SessionStatus.POST_SESSION: {SessionStatus.COMPLETE},
    SessionStatus.COMPLETE: set(),
}

# Active-play transitions are driven by the pipeline. TABLE_OPEN is the hub.
_HUB = ActivePlayState.TABLE_OPEN
_ACTIVE_PLAY_TRANSITIONS: dict[ActivePlayState, set[ActivePlayState]] = {
    ActivePlayState.SCENE_FRAMING: {_HUB},
    _HUB: {
        ActivePlayState.ADJUDICATING,
        ActivePlayState.CLARIFICATION_REQUIRED,
        ActivePlayState.SCENE_TRANSITION,
        ActivePlayState.COMBAT_INITIALIZING,
    },
    ActivePlayState.CLARIFICATION_REQUIRED: {ActivePlayState.ADJUDICATING, _HUB},
    ActivePlayState.ADJUDICATING: {ActivePlayState.RESOLVING, ActivePlayState.CLARIFICATION_REQUIRED, _HUB},
    ActivePlayState.RESOLVING: {ActivePlayState.COMMITTING_STATE},
    ActivePlayState.COMMITTING_STATE: {ActivePlayState.NARRATING},
    ActivePlayState.NARRATING: {_HUB, ActivePlayState.SCENE_TRANSITION},
    ActivePlayState.SCENE_TRANSITION: {ActivePlayState.SCENE_FRAMING, _HUB},
    ActivePlayState.COMBAT_INITIALIZING: {ActivePlayState.COMBAT_ACTIVE},
    ActivePlayState.COMBAT_ACTIVE: {ActivePlayState.COMBAT_RESOLVING_TURN, _HUB},
    ActivePlayState.COMBAT_RESOLVING_TURN: {ActivePlayState.COMBAT_ACTIVE, _HUB},
}


def assert_session_transition(current: str, target: SessionStatus) -> None:
    cur = SessionStatus(current)
    if target not in _SESSION_TRANSITIONS[cur]:
        raise IllegalStateTransition(f"session cannot go {cur.value} -> {target.value}")


def assert_active_play_transition(current: str, target: ActivePlayState) -> None:
    cur = ActivePlayState(current)
    if target == cur:
        return
    if target not in _ACTIVE_PLAY_TRANSITIONS.get(cur, set()):
        raise IllegalStateTransition(
            f"active-play cannot go {cur.value} -> {target.value}"
        )


class SessionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_session(
        self, *, campaign_id: str, attendance: list[str] | None = None
    ) -> Session:
        count = (
            await self.session.execute(
                select(func.count(Session.id)).where(Session.campaign_id == campaign_id)
            )
        ).scalar_one()
        row = Session(
            campaign_id=campaign_id,
            number=count + 1,
            status=SessionStatus.PREPARATION.value,
            active_play_state=ActivePlayState.TABLE_OPEN.value,
            attendance=attendance or [],
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_session(self, session_id: str) -> Session:
        row = await self.session.get(Session, session_id)
        if row is None:
            raise NotFoundError(f"session {session_id} not found")
        return row

    async def get_active_session(self, campaign_id: str) -> Session | None:
        return (
            await self.session.execute(
                select(Session)
                .where(
                    Session.campaign_id == campaign_id,
                    Session.status.in_(
                        [
                            SessionStatus.OPENING.value,
                            SessionStatus.ACTIVE_PLAY.value,
                            SessionStatus.CLOSING.value,
                        ]
                    ),
                )
                .order_by(Session.number.desc())
            )
        ).scalars().first()

    async def transition_status(self, session_id: str, target: SessionStatus) -> Session:
        row = await self.get_session(session_id)
        assert_session_transition(row.status, target)
        row.status = target.value
        if target in (SessionStatus.OPENING, SessionStatus.ACTIVE_PLAY) and row.started_at is None:
            row.started_at = utcnow()
        if target == SessionStatus.CLOSING and row.ended_at is None:
            row.ended_at = utcnow()
        row.version += 1
        return row

    async def open_session(self, session_id: str) -> Session:
        return await self.transition_status(session_id, SessionStatus.OPENING)

    async def begin_active_play(self, session_id: str) -> Session:
        row = await self.transition_status(session_id, SessionStatus.ACTIVE_PLAY)
        row.active_play_state = ActivePlayState.SCENE_FRAMING.value
        return row

    async def start_session(self, session_id: str) -> Session:
        """Convenience: PREPARATION -> ACTIVE_PLAY with scene framing."""
        row = await self.transition_status(session_id, SessionStatus.ACTIVE_PLAY)
        row.active_play_state = ActivePlayState.SCENE_FRAMING.value
        return row

    async def set_active_play_state(self, session_id: str, target: ActivePlayState) -> Session:
        row = await self.get_session(session_id)
        assert_active_play_transition(row.active_play_state, target)
        row.active_play_state = target.value
        row.version += 1
        return row

    async def close_session(self, session_id: str) -> Session:
        return await self.transition_status(session_id, SessionStatus.CLOSING)
