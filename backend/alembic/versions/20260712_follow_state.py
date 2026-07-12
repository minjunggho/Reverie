"""Explicit travel-consent (follow) state on characters (§18 movement consent).

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
    # Deferred from E7: planned_subclass had no migration (dev SQLite used
    # create_all). Add it here so a Postgres deploy has the column. Guarded so it is
    # a no-op where create_all already made it.
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("characters")}
    if "planned_subclass" not in cols:
        op.add_column("characters",
                      sa.Column("planned_subclass", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("characters", "following_character_id")
