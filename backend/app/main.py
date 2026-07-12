"""FastAPI application factory.

Exposes health/admin endpoints. Game logic lives in the engine (services/tabletop),
never in HTTP handlers.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app import __version__
from app.api.activity import router as activity_router
from app.api.admin import router as admin_router
from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_database
from app.rules_content import get_registry

# Production Activity frontend build (activity/dist), served same-origin at /activity.
_ACTIVITY_DIST = Path(__file__).resolve().parents[2] / "activity" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    # Fail before accepting traffic if character creation cannot be completed
    # from the deployed rules/UI content.
    get_registry()
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
    app.include_router(activity_router)

    if _ACTIVITY_DIST.is_dir():  # production frontend build present
        from fastapi.staticfiles import StaticFiles

        app.mount("/activity/assets",
                  StaticFiles(directory=_ACTIVITY_DIST / "assets"), name="activity-assets")

        @app.get("/activity")
        @app.get("/activity/{path:path}")
        async def activity_spa(path: str = "") -> FileResponse:
            # SPA fallback: every /activity route serves the single-page shell.
            return FileResponse(_ACTIVITY_DIST / "index.html")

    return app


app = create_app()
