"""Read-only admin/debug endpoints for observability. No game mutations here."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.db.session import get_database
from app.models.campaign import Campaign
from app.models.event import Event
from app.services.campaigns import CampaignService
from app.services.sessions import SessionService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/campaigns/by-channel/{channel_id}")
async def campaign_by_channel(channel_id: str) -> dict:
    db = get_database()
    async with db.session() as session:
        campaign = await CampaignService(session).resolve_campaign_by_channel(channel_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="no campaign for channel")
        active = await SessionService(session).get_active_session(campaign.id)
        event_count = (
            await session.execute(
                select(func.count(Event.id)).where(Event.campaign_id == campaign.id)
            )
        ).scalar_one()
        return {
            "campaign_id": campaign.id,
            "name": campaign.name,
            "status": campaign.status,
            "current_game_time": campaign.current_game_time,
            "active_session_id": active.id if active else None,
            "active_session_status": active.status if active else None,
            "event_count": event_count,
        }


@router.get("/campaigns/{campaign_id}/events")
async def recent_events(campaign_id: str, limit: int = 25) -> dict:
    db = get_database()
    async with db.session() as session:
        if await session.get(Campaign, campaign_id) is None:
            raise HTTPException(status_code=404, detail="campaign not found")
        rows = list(
            (
                await session.execute(
                    select(Event)
                    .where(Event.campaign_id == campaign_id)
                    .order_by(Event.seq.desc())
                    .limit(limit)
                )
            ).scalars()
        )
        return {
            "campaign_id": campaign_id,
            "events": [
                {
                    "seq": e.seq,
                    "type": e.event_type,
                    "visibility": e.visibility,
                    "summary": (e.payload or {}).get("summary", ""),
                }
                for e in reversed(rows)
            ],
        }
