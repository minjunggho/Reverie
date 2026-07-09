"""Location CRUD. Description layers feed the retrieval layer (obvious vs hidden)."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.location import Location


class LocationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_location(
        self,
        *,
        campaign_id: str,
        name: str,
        description_obvious: str = "",
        description_focused: str = "",
        description_hidden: str = "",
        connections: list[str] | None = None,
        contents: list[str] | None = None,
    ) -> Location:
        loc = Location(
            campaign_id=campaign_id,
            name=name,
            description_obvious=description_obvious,
            description_focused=description_focused,
            description_hidden=description_hidden,
            connections=connections or [],
            contents=contents or [],
        )
        self.session.add(loc)
        await self.session.flush()
        return loc

    async def get_location(self, location_id: str) -> Location:
        loc = await self.session.get(Location, location_id)
        if loc is None:
            raise NotFoundError(f"location {location_id} not found")
        return loc
