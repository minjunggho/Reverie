"""User — a Discord user known to the engine. Canonical."""
from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, pk_column


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = pk_column()
    discord_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
