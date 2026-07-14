"""Explicit campaign-local pantheon activation.

New and existing campaigns begin with no active pantheon. Content packs remain
available in the static registry until a campaign owner explicitly activates one.

Revision ID: 20260717_pantheons
Revises: 20260716_mainstory
"""
from alembic import op
import sqlalchemy as sa

revision = "20260717_pantheons"
down_revision = "20260716_mainstory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column(
            "active_pantheon_keys",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("campaigns", "active_pantheon_keys")
