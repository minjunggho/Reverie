"""EventService — the ONLY sanctioned writer of the canonical event log.

`record()` appends an Event and assigns a per-campaign monotonic `seq`. Because the
service operates within the caller's transaction (it does not commit), a state
mutation and the Event(s) that record it commit or roll back together — atomicity is
a property of the enclosing `unit_of_work`, proven in the Phase 3 tests.

Reading is visibility-aware: `list_visible_events` is what player-facing context
builders use so restricted events physically cannot be selected into a player prompt.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.campaign import Campaign
from app.models.enums import EventType, Visibility
from app.models.event import Event


class EventService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _next_seq(self, campaign_id: str) -> int:
        # Atomic increment at the DB level within the current transaction.
        result = await self.session.execute(
            update(Campaign)
            .where(Campaign.id == campaign_id)
            .values(event_seq=Campaign.event_seq + 1)
        )
        if result.rowcount != 1:
            raise NotFoundError(f"campaign {campaign_id} not found")
        return (
            await self.session.execute(
                select(Campaign.event_seq).where(Campaign.id == campaign_id)
            )
        ).scalar_one()

    async def record(
        self,
        *,
        campaign_id: str,
        event_type: EventType,
        session_id: str | None = None,
        scene_id: str | None = None,
        campaign_time: int | None = None,
        actor_entity: str | None = None,
        target_entities: list[str] | None = None,
        location_id: str | None = None,
        witnesses: list[str] | None = None,
        visibility: Visibility = Visibility.PARTY,
        payload: dict[str, Any] | None = None,
        mechanical_changes: dict[str, Any] | None = None,
        narrative_significance: int = 0,
    ) -> Event:
        if campaign_time is None:
            campaign = await self.session.get(Campaign, campaign_id)
            if campaign is None:
                raise NotFoundError(f"campaign {campaign_id} not found")
            campaign_time = campaign.current_game_time

        seq = await self._next_seq(campaign_id)
        event = Event(
            seq=seq,
            campaign_id=campaign_id,
            session_id=session_id,
            scene_id=scene_id,
            event_type=event_type.value,
            campaign_time=campaign_time,
            actor_entity=actor_entity,
            target_entities=target_entities or [],
            location_id=location_id,
            witnesses=witnesses or [],
            visibility=visibility.value,
            payload=payload or {},
            mechanical_changes=mechanical_changes or {},
            narrative_significance=narrative_significance,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    # --- reads ---------------------------------------------------------------
    async def list_events(
        self,
        *,
        campaign_id: str,
        session_id: str | None = None,
        event_types: Iterable[EventType] | None = None,
    ) -> list[Event]:
        stmt = select(Event).where(Event.campaign_id == campaign_id)
        if session_id is not None:
            stmt = stmt.where(Event.session_id == session_id)
        if event_types is not None:
            stmt = stmt.where(Event.event_type.in_([e.value for e in event_types]))
        stmt = stmt.order_by(Event.seq.asc())
        return list((await self.session.execute(stmt)).scalars())

    async def list_visible_events(
        self,
        *,
        campaign_id: str,
        allowed_visibilities: Sequence[Visibility],
        session_id: str | None = None,
    ) -> list[Event]:
        """Retrieval-layer read: only events whose visibility is in the allowed set.

        This is the structural guarantee behind player-safe recaps — a `DM_ONLY`
        event is filtered out by the SQL WHERE clause, so it can never reach the
        recap context builder in the first place.
        """
        allowed = [v.value for v in allowed_visibilities]
        stmt = select(Event).where(
            Event.campaign_id == campaign_id,
            Event.visibility.in_(allowed),
        )
        if session_id is not None:
            stmt = stmt.where(Event.session_id == session_id)
        stmt = stmt.order_by(Event.seq.asc())
        return list((await self.session.execute(stmt)).scalars())
