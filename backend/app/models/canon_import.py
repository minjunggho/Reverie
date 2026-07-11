"""Owner-reviewed campaign import source and extraction proposal."""
from __future__ import annotations

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column


class CanonImport(Base, TimestampMixin):
    __tablename__ = "canon_imports"

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    uploader_member_id: Mapped[str] = fk_id("campaign_members.id")
    filename: Mapped[str] = mapped_column(String(255))
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="PENDING_REVIEW")
    proposal: Mapped[dict] = mapped_column(JSON, default=dict)
    errors: Mapped[list[str]] = mapped_column(JSON, default=list)
