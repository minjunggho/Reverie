"""Active subclass (distinct from the narrative planned_subclass).

`planned_subclass` is a non-mechanical preference chosen at creation.
`active_subclass` is granted only at the class's subclass_level after the player
confirms — it is what activates subclass features. Existing characters get NULL
(no active subclass), and any existing planned_subclass stays non-mechanical.

Revision ID: 20260715_subclass
Revises: 20260714_identity
"""
from alembic import op
import sqlalchemy as sa

revision = "20260715_subclass"
down_revision = "20260714_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("characters",
                  sa.Column("active_subclass", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("characters", "active_subclass")
