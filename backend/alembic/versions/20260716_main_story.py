"""Main-story continuity state on campaigns.

Tracks the main dramatic question, current state, leads, hidden truth, deadlines,
and player-caused branches/goal outcomes so the imported main story keeps reacting
across many turns and restarts. Existing campaigns migrate to an empty {} (no main
story tracked yet) — safe and non-mechanical until populated at import.

Revision ID: 20260716_mainstory
Revises: 20260715_subclass
"""
from alembic import op
import sqlalchemy as sa

revision = "20260716_mainstory"
down_revision = "20260715_subclass"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaigns",
                  sa.Column("main_story", sa.JSON(), nullable=False,
                            server_default=sa.text("'{}'")))


def downgrade() -> None:
    op.drop_column("campaigns", "main_story")
