"""NPC episodic memory + relationship dimensions (§7, §10).

Revision ID: 20260712_npcmem
Revises: 20260712_follow
"""
from alembic import op
import sqlalchemy as sa

revision = "20260712_npcmem"
down_revision = "20260712_follow"
branch_labels = None
depends_on = None

_REL_COLS = [
    ("familiarity", sa.Integer, "0"),
    ("affection", sa.Integer, "0"),
    ("respect", sa.Integer, "0"),
    ("fear", sa.Integer, "0"),
    ("anger", sa.Integer, "0"),
    ("suspicion", sa.Integer, "0"),
    ("obligation", sa.Integer, "0"),
]


def upgrade() -> None:
    for name, coltype, default in _REL_COLS:
        op.add_column("npc_relationships",
                      sa.Column(name, coltype(), nullable=False, server_default=default))
    op.add_column("npc_relationships",
                  sa.Column("current_stance", sa.String(40), nullable=False, server_default="neutral"))
    op.add_column("npc_relationships",
                  sa.Column("last_interaction_event_id", sa.String(32), nullable=True))

    op.create_table(
        "npc_memories",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("npc_id", sa.String(32), sa.ForeignKey("npcs.id"), nullable=False),
        sa.Column("subject_ref", sa.String(80), nullable=False),
        sa.Column("event_id", sa.String(32), nullable=True),
        sa.Column("memory_type", sa.String(24), nullable=False, server_default="INTERACTION"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("importance", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("emotional_valence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("witnessed_directly", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source_ref", sa.String(80), nullable=True),
        sa.Column("location_id", sa.String(32), nullable=True),
        sa.Column("game_time", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_recalled_at", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_npc_memories_npc_subject", "npc_memories", ["npc_id", "subject_ref"])


def downgrade() -> None:
    op.drop_index("ix_npc_memories_npc_subject", table_name="npc_memories")
    op.drop_table("npc_memories")
    op.drop_column("npc_relationships", "last_interaction_event_id")
    op.drop_column("npc_relationships", "current_stance")
    for name, _, _ in reversed(_REL_COLS):
        op.drop_column("npc_relationships", name)
