"""A scene knows when nothing is happening in it.

Nothing evaluated whether a turn accomplished anything, so a scene ran forever and the
world had no reason to lean in. `app/scenes` — the module scene_service's docstring
pointed at for exhaustion/transition logic — was never built.

Both columns are additive with safe defaults: an existing scene starts at zero dead
turns with its purpose unspent, which is exactly its current meaning.

Revision ID: 20260726_scene_pacing
Revises: 20260725_clues
"""
from alembic import op
import sqlalchemy as sa

revision = "20260726_scene_pacing"
down_revision = "20260725_clues"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scenes", sa.Column(
        "low_progress_turns", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("scenes", sa.Column(
        "purpose_satisfied", sa.Boolean(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    op.drop_column("scenes", "purpose_satisfied")
    op.drop_column("scenes", "low_progress_turns")
