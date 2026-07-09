"""Create/manage threats and scheduled world events (the setup side of §21)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.world import ScheduledWorldEvent, Threat


class ThreatService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_threat(
        self, *, campaign_id: str, name: str, goal: str = "",
        scheduled_game_time: int = 0, tick_amount: int = 10, tick_interval: int = 240,
        next_action: str = "", progress: int = 0,
    ) -> Threat:
        threat = Threat(
            campaign_id=campaign_id, name=name, goal=goal, progress=progress,
            next_action=next_action, scheduled_game_time=scheduled_game_time,
            tick_amount=tick_amount, tick_interval=tick_interval,
        )
        self.session.add(threat)
        await self.session.flush()
        return threat

    async def schedule_event(
        self, *, campaign_id: str, due_game_time: int, kind: str = "generic",
        payload: dict[str, Any] | None = None, perceivable: bool = False,
    ) -> ScheduledWorldEvent:
        ev = ScheduledWorldEvent(
            campaign_id=campaign_id, due_game_time=due_game_time, kind=kind,
            payload=payload or {}, perceivable=perceivable,
        )
        self.session.add(ev)
        await self.session.flush()
        return ev
