"""NPC innate biases — a specific person's predispositions toward an ancestry,
class, faction, culture, or religion, separate from EARNED per-character
relationships (which stay in npc_relationships, untouched).

Existing NPCs have no biases (NULL) — the safe default; no NPC gains prejudice
without explicit data, and the campaign's bias level still gates whether any bias is
ever expressed.

Revision ID: 20260720_npc_biases
Revises: 20260719_geography
"""
from alembic import op
import sqlalchemy as sa

revision = "20260720_npc_biases"
down_revision = "20260719_geography"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("npcs", sa.Column("biases", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("npcs", "biases")
