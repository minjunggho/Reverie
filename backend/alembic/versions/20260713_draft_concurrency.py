"""Draft optimistic locking + one-active-draft-per-member constraint.

`version` makes every draft save a compare-and-update, so concurrent writers
(multi-process deployments, double-delivered Discord interactions) cannot
silently overwrite each other. The partial unique index enforces at most one
ACTIVE draft per campaign member at the database level.

Revision ID: 20260713_draftver
Revises: 20260712_npcmem
"""
from alembic import op
import sqlalchemy as sa

revision = "20260713_draftver"
down_revision = "20260712_npcmem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "character_drafts",
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "uq_character_drafts_active_member",
        "character_drafts",
        ["campaign_id", "member_id"],
        unique=True,
        sqlite_where=sa.text("status = 'ACTIVE'"),
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index("uq_character_drafts_active_member", table_name="character_drafts")
    op.drop_column("character_drafts", "version")
