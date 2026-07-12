"""Explicit travel-consent (follow) state + planned subclass on characters.

`following_character_id` is the persistent movement-consent record (§18): a
character travels along with another ONLY while this points at them. Co-location
is never consent. `planned_subclass` records the player's Stage-B subclass plan
(selection activates mechanically at level 3).

Revision ID: 20260712_follow
Revises: 20260711_economy
"""
from alembic import op
import sqlalchemy as sa

revision = "20260712_follow"
down_revision = "20260711_economy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("characters",
                  sa.Column("following_character_id", sa.String(32), nullable=True))
    op.add_column("characters",
                  sa.Column("planned_subclass", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("characters", "planned_subclass")
    op.drop_column("characters", "following_character_id")
