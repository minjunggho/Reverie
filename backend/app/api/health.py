"""Health / readiness endpoints."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app import __version__
from app.core.config import get_settings
from app.db.session import get_database

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/health/db")
async def health_db() -> dict:
    settings = get_settings()
    db = get_database(settings)
    async with db.session() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable", "provider": settings.llm_provider}
