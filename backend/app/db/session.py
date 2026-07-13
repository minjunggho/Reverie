"""Async engine, session factory, and the unit-of-work transaction helper.

`unit_of_work` is the single sanctioned way the engine mutates canonical state: it
opens a transaction, yields a session, and commits — so a state change and the
Event(s) recording it either commit together or roll back together (see
`docs/event-model.md`).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base


def _make_engine(url: str, echo: bool) -> AsyncEngine:
    kwargs: dict = {"echo": echo, "future": True}
    if url.startswith("sqlite"):
        # Share a single in-memory connection across the session pool and allow
        # cross-thread use (aiosqlite runs in a worker thread).
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    engine = create_async_engine(url, **kwargs)

    if url.startswith("sqlite"):
        # Enforce foreign keys on SQLite (off by default) so FK/cascade tests are real.
        @event.listens_for(engine.sync_engine, "connect")
        def _fk_pragma(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


class Database:
    """Owns an async engine + session factory for one connection target."""

    def __init__(self, url: str, echo: bool = False) -> None:
        self.url = url
        self.engine = _make_engine(url, echo)
        self.sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False, autoflush=False,
        )

    async def create_all(self) -> None:
        # Import models so they are registered on Base.metadata before create_all.
        import app.models  # noqa: F401

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            if self.url.startswith("sqlite"):
                await conn.run_sync(self._ensure_sqlite_compat_columns)

    # Columns added to EXISTING tables after developers had already created a
    # local SQLite file. `create_all()` will not alter existing tables, so this
    # dev-only compatibility step adds whatever is missing on startup.
    # Production never runs this — it migrates with Alembic.
    _SQLITE_COMPAT_COLUMNS: dict[str, list[tuple[str, str]]] = {
        "campaigns": [
            ("starting_location_id", "VARCHAR(32)"),
            ("current_party_anchor_id", "VARCHAR(32)"),
            ("default_session_opening", "TEXT NOT NULL DEFAULT ''"),
            ("world_model_version", "INTEGER NOT NULL DEFAULT 2"),
        ],
        "characters": [
            ("planned_subclass", "VARCHAR(80)"),
            ("active_subclass", "VARCHAR(80)"),
            ("following_character_id", "VARCHAR(32)"),
            ("origin_text", "TEXT NOT NULL DEFAULT ''"),
            ("identity", "JSON NOT NULL DEFAULT '{}'"),
        ],
        "character_drafts": [
            ("version", "INTEGER NOT NULL DEFAULT 0"),
        ],
        "npc_relationships": [
            ("familiarity", "INTEGER NOT NULL DEFAULT 0"),
            ("affection", "INTEGER NOT NULL DEFAULT 0"),
            ("respect", "INTEGER NOT NULL DEFAULT 0"),
            ("fear", "INTEGER NOT NULL DEFAULT 0"),
            ("anger", "INTEGER NOT NULL DEFAULT 0"),
            ("suspicion", "INTEGER NOT NULL DEFAULT 0"),
            ("obligation", "INTEGER NOT NULL DEFAULT 0"),
            ("current_stance", "VARCHAR(40) NOT NULL DEFAULT 'neutral'"),
            ("last_interaction_event_id", "VARCHAR(32)"),
        ],
    }

    def _ensure_sqlite_compat_columns(self, conn) -> None:
        """Backfill missing columns for older local SQLite databases."""
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        for table, columns in self._SQLITE_COMPAT_COLUMNS.items():
            if table not in table_names:
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, ddl_type in columns:
                if name not in existing:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"
                    ))

    async def drop_all(self) -> None:
        import app.models  # noqa: F401

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def dispose(self) -> None:
        await self.engine.dispose()

    def session(self) -> AsyncSession:
        return self.sessionmaker()

    @asynccontextmanager
    async def unit_of_work(self) -> AsyncIterator[AsyncSession]:
        """Transaction boundary: commit on success, roll back on any exception."""
        session = self.sessionmaker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Process-wide default database (used by the API / live bot). Tests construct their
# own `Database` against in-memory SQLite and do not touch this singleton.
_default_db: Database | None = None


def get_database(settings: Settings | None = None) -> Database:
    global _default_db
    if _default_db is None:
        settings = settings or get_settings()
        _default_db = Database(settings.database_url, echo=settings.db_echo)
    return _default_db


@asynccontextmanager
async def unit_of_work() -> AsyncIterator[AsyncSession]:
    async with get_database().unit_of_work() as session:
        yield session
