"""Add reviewed campaign canon imports.

Revision ID: 20260710_canon
Revises: 20260710_aliases
"""
from alembic import op
import sqlalchemy as sa

revision = "20260710_canon"
down_revision = "20260710_aliases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canon_imports",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("campaign_id", sa.String(32), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("uploader_member_id", sa.String(32), sa.ForeignKey("campaign_members.id"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING_REVIEW"),
        sa.Column("proposal", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("errors", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_canon_imports_content_sha256", "canon_imports", ["content_sha256"])


def downgrade() -> None:
    op.drop_index("ix_canon_imports_content_sha256", table_name="canon_imports")
    op.drop_table("canon_imports")
