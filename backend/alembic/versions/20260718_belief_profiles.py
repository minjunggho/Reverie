"""Persistent character and NPC belief profiles.

Existing rows remain valid with no belief. Cleric mechanics are stored separately
from personal religious identity.

Revision ID: 20260718_beliefs
Revises: 20260717_pantheons
"""
from alembic import op
import sqlalchemy as sa

revision = "20260718_beliefs"
down_revision = "20260717_pantheons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("characters", sa.Column("belief_profile", sa.JSON(), nullable=True))
    op.add_column("characters", sa.Column("cleric_deity_key", sa.String(80), nullable=True))
    op.add_column("characters", sa.Column("cleric_domain", sa.String(40), nullable=True))
    op.add_column("npcs", sa.Column("belief_profile", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("npcs", "belief_profile")
    op.drop_column("characters", "cleric_domain")
    op.drop_column("characters", "cleric_deity_key")
    op.drop_column("characters", "belief_profile")
