"""FastAPI application factory.

Exposes health/admin endpoints. Game logic lives in the engine (services/tabletop),
never in HTTP handlers.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.admin import router as admin_router
from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    db = get_database(settings)
    # For local/dev SQLite convenience, ensure tables exist. Production uses Alembic.
    if settings.is_sqlite:
        await db.create_all()
    yield
    await db.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Reverie DM Engine", version=__version__, lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(admin_router)
    return app


app = create_app()
