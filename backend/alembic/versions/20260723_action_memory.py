"""NPC memories can carry an unresolved question.

A memory could record what happened but not that the NPC is still waiting for an
explanation. So a player could be caught reaching for the map, change the subject, and
the NPC had no structural reason to ever raise it again — the thread simply stopped
existing.

Both columns are additive with safe defaults: every existing memory keeps its exact
meaning, asking nothing and resolving nothing.

Revision ID: 20260723_action_memory
Revises: 20260722_spell_effects
"""
from alembic import op
import sqlalchemy as sa

revision = "20260723_action_memory"
down_revision = "20260722_spell_effects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("npc_memories", sa.Column(
        "open_question", sa.Text(), nullable=False, server_default=""))
    op.add_column("npc_memories", sa.Column(
        "resolved", sa.Boolean(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    op.drop_column("npc_memories", "resolved")
    op.drop_column("npc_memories", "open_question")
