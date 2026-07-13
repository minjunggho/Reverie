"""Preserve original creation text + structured character identity.

Character creation must keep BOTH the complete player-authored text and a
structured identity extracted from it — never reduce a player's investment to a
single summary. `origin_text` holds the verbatim source; `identity` holds the
extracted fields + reviewable evolution seeds.

Revision ID: 20260714_identity
Revises: 20260713_draftver
"""
from alembic import op
import sqlalchemy as sa

revision = "20260714_identity"
down_revision = "20260713_draftver"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("characters",
                  sa.Column("origin_text", sa.Text(), nullable=False, server_default=""))
    op.add_column("characters",
                  sa.Column("identity", sa.JSON(), nullable=False,
                            server_default=sa.text("'{}'")))


def downgrade() -> None:
    op.drop_column("characters", "identity")
    op.drop_column("characters", "origin_text")
