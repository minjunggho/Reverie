"""Declarative base and portable column helpers.

The ORM is intentionally dialect-portable so the test suite runs on SQLite while
production runs on PostgreSQL:
- Primary/foreign keys are 32-char hex strings (`String(32)`), generated in Python.
- JSON columns use SQLAlchemy's generic `JSON` type (JSONB on PG, JSON-text on SQLite).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.clock import utcnow
from app.core.ids import new_id


class Base(DeclarativeBase):
    pass


# Convenient column factories -------------------------------------------------

def pk_column() -> Mapped[str]:
    return mapped_column(String(32), primary_key=True, default=new_id)


def fk_id(target: str, *, nullable: bool = False, **kw: Any) -> Mapped[str]:
    from sqlalchemy import ForeignKey

    return mapped_column(String(32), ForeignKey(target, ondelete="CASCADE"),
                         nullable=nullable, **kw)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
