"""Explicit character aliases for entity resolution (Thai transliterations).

Reconstructed 2026-07-12: the shipped ``20260710_canon`` migration declared this
revision as its parent, but the file was never committed to the repository. Git
history confirms no alembic version file by this name ever existed. It is
recreated here with the schema change its era introduced — the
``characters.aliases`` JSON column (commit 151bf69 wired alias-based entity
resolution) — so the recorded chain is whole again without editing the shipped
canon migration.

Revision ID: 20260710_aliases
Revises: 20260708_core
"""
from alembic import op
import sqlalchemy as sa

revision = "20260710_aliases"
down_revision = "20260708_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column("aliases", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("characters", "aliases")
