"""PositionService — canonical "where is each character" + co-location.

Scene presence derives from position (party splits are just different location_ids).
Moving a character is a domain op paired with a CHARACTER_MOVED event.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import entity_ref
from app.models.character import Character
from app.models.enums import EventType, Visibility
from app.services.events import EventService


class PositionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def place(self, *, character_id: str, location_id: str) -> None:
        char = await self.session.get(Character, character_id)
        if char is not None:
            char.location_id = location_id

    async def where_is(self, character_id: str) -> str | None:
        char = await self.session.get(Character, character_id)
        return char.location_id if char else None

    async def co_located(self, *, campaign_id: str, location_id: str) -> list[Character]:
        """All player characters physically present at a location."""
        return list((await self.session.execute(
            select(Character).where(
                Character.campaign_id == campaign_id,
                Character.location_id == location_id)
        )).scalars())

    async def set_follow(self, *, follower_id: str, leader_id: str) -> None:
        """Record explicit travel consent: `follower` agrees to travel with `leader`.
        A character never follows itself. This is the ONLY way a character other than
        the actor moves during travel (besides an involuntary physical effect)."""
        if follower_id == leader_id:
            return
        follower = await self.session.get(Character, follower_id)
        if follower is not None:
            follower.following_character_id = leader_id

    async def stop_follow(self, *, follower_id: str) -> None:
        follower = await self.session.get(Character, follower_id)
        if follower is not None:
            follower.following_character_id = None

    async def consenting_followers(
        self, *, campaign_id: str, leader_id: str, at_location_id: str
    ) -> list[str]:
        """Character ids that have explicitly agreed to travel with `leader` AND are
        co-located with them right now. Someone who wandered off (different location)
        is not dragged along; someone who never consented is never moved."""
        rows = (await self.session.execute(
            select(Character).where(
                Character.campaign_id == campaign_id,
                Character.following_character_id == leader_id,
                Character.location_id == at_location_id,
            )
        )).scalars()
        return [c.id for c in rows if c.id != leader_id]

    async def move(
        self, *, character_id: str, to_location_id: str, campaign_id: str,
        session_id: str | None = None, from_location_id: str | None = None,
        game_time: int | None = None,
    ) -> None:
        char = await self.session.get(Character, character_id)
        if char is None:
            return
        before = from_location_id or char.location_id
        char.location_id = to_location_id
        await EventService(self.session).record(
            campaign_id=campaign_id, session_id=session_id,
            event_type=EventType.CHARACTER_MOVED,
            actor_entity=entity_ref("character", character_id),
            location_id=to_location_id, campaign_time=game_time,
            visibility=Visibility.PARTY,
            payload={"from": before, "to": to_location_id,
                     "summary": f"{char.name} ย้ายที่"},
            narrative_significance=10,
        )
