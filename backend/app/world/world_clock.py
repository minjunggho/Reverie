"""World clock + scheduler (§21).

`advance_time` is the single engine-owned path that moves in-world time. Whenever
time advances it ticks any DUE threats and fires any DUE scheduled events through
domain writes + canonical events. The LLM never remembers or advances the clock; it
only narrates player-perceivable consequences afterward.

Design note: advancing time does NOT itself punish the party. Only threats whose own
schedule has come due tick, and their effects are DM-scoped unless perceivable — so
"not every rest is dangerous" (configurable via campaign `punish_every_rest`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.campaign import Campaign
from app.models.enums import EventType, Visibility
from app.models.world import ScheduledWorldEvent, Threat
from app.services.events import EventService


@dataclass
class TimeAdvanceResult:
    minutes: int
    new_game_time: int
    ticked_threats: list[str] = field(default_factory=list)
    fired_events: list[str] = field(default_factory=list)
    perceivable_notes: list[str] = field(default_factory=list)


class WorldClockService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.events = EventService(session)

    async def advance_time(
        self, *, campaign_id: str, minutes: int, session_id: str | None = None,
        scene_id: str | None = None, actor_entity: str | None = None,
    ) -> TimeAdvanceResult:
        if minutes <= 0:
            raise ValueError("advance_time requires minutes > 0")
        campaign = await self.session.get(Campaign, campaign_id)
        if campaign is None:
            raise NotFoundError(f"campaign {campaign_id} not found")
        before = campaign.current_game_time
        after = before + minutes
        campaign.current_game_time = after

        await self.events.record(
            campaign_id=campaign_id, session_id=session_id, scene_id=scene_id,
            event_type=EventType.WORLD_TIME_ADVANCED, actor_entity=actor_entity,
            campaign_time=after, visibility=Visibility.PARTY,
            mechanical_changes={"game_time": {"from": before, "to": after}},
            narrative_significance=10,
        )

        result = TimeAdvanceResult(minutes=minutes, new_game_time=after)
        await self._tick_threats(campaign_id, after, session_id, result)
        await self._fire_events(campaign_id, after, session_id, result)
        return result

    async def _tick_threats(self, campaign_id, now, session_id, result) -> None:
        due = (
            await self.session.execute(
                select(Threat).where(
                    Threat.campaign_id == campaign_id,
                    Threat.status == "active",
                    Threat.scheduled_game_time <= now,
                )
            )
        ).scalars().all()
        for threat in due:
            before = threat.progress
            threat.progress = min(100, before + threat.tick_amount)
            threat.scheduled_game_time = now + threat.tick_interval
            if threat.progress >= 100:
                threat.status = "resolved"
            # Threat advancement is DM-scoped; players learn of it only if perceivable.
            await self.events.record(
                campaign_id=campaign_id, session_id=session_id,
                event_type=EventType.THREAT_ADVANCED, actor_entity=f"threat:{threat.id}",
                campaign_time=now, visibility=Visibility.DM_ONLY,
                mechanical_changes={"progress": {"from": before, "to": threat.progress}},
                payload={"name": threat.name, "next_action": threat.next_action},
                narrative_significance=25,
            )
            result.ticked_threats.append(threat.id)

    async def _fire_events(self, campaign_id, now, session_id, result) -> None:
        due = (
            await self.session.execute(
                select(ScheduledWorldEvent).where(
                    ScheduledWorldEvent.campaign_id == campaign_id,
                    ScheduledWorldEvent.resolved.is_(False),
                    ScheduledWorldEvent.due_game_time <= now,
                )
            )
        ).scalars().all()
        for ev in due:
            ev.resolved = True  # exactly-once: a fired event is never selected again
            visibility = Visibility.PARTY if ev.perceivable else Visibility.DM_ONLY
            await self.events.record(
                campaign_id=campaign_id, session_id=session_id,
                event_type=EventType.WORLD_TIME_ADVANCED, actor_entity="system",
                campaign_time=now, visibility=visibility,
                payload={"scheduled_event": ev.kind, **(ev.payload or {})},
                narrative_significance=20,
            )
            result.fired_events.append(ev.id)
            if ev.perceivable:
                note = (ev.payload or {}).get("summary", ev.kind)
                result.perceivable_notes.append(note)
            # A due delayed consequence performs its concrete world write now — exactly
            # once, on this same authoritative time-advance path. Unknown kinds remain
            # plain scheduled markers (back-compat).
            await self._dispatch_consequence(campaign_id, ev, now, session_id)

    async def _dispatch_consequence(self, campaign_id, ev, now, session_id) -> None:
        """Turn a fired ScheduledWorldEvent into the real consequence it stands for.

        Kept additive and gated on a known-kind set so an arbitrary scheduled event
        (a storm, a spy report) still fires as a generic marker. All dispatches reuse
        ConsequenceService — no parallel consequence engine lives here."""
        payload = ev.payload or {}
        kind = ev.kind
        known = {"rumor_spread", "faction_action", "threat_action",
                 "npc_availability", "guard_response"}
        if kind not in known:
            return
        from app.world.consequence_service import ConsequenceService

        cs = ConsequenceService(
            self.session, campaign_id=campaign_id, session_id=session_id,
            actor_entity="system",
        )
        if kind == "rumor_spread" and payload.get("rumor_id"):
            await cs.widen_rumor(rumor_id=payload["rumor_id"])
        elif kind == "faction_action" and payload.get("faction_id"):
            await cs.advance_faction(
                faction_id=payload["faction_id"],
                progress_delta=int(payload.get("progress_delta", 0)),
                disposition_delta=int(payload.get("disposition_delta", 0)),
                next_in_minutes=payload.get("next_in_minutes"),
                current_game_time=now, note=payload.get("note", ""),
            )
        elif kind == "threat_action" and payload.get("threat_id"):
            await cs.update_threat(
                threat_id=payload["threat_id"],
                progress_delta=int(payload.get("progress_delta", 0)),
                next_in_minutes=payload.get("next_in_minutes"),
                current_game_time=now, note=payload.get("note", ""),
            )
        elif kind == "npc_availability" and payload.get("npc_id"):
            await cs.set_npc_available(
                npc_id=payload["npc_id"],
                available=bool(payload.get("available", False)),
                reason=payload.get("reason", ""),
            )
        elif kind == "guard_response" and payload.get("npc_id"):
            await cs.move_npc(
                npc_id=payload["npc_id"], to_location_id=payload.get("to_location_id"),
                reason=payload.get("reason", "guard response"),
            )
