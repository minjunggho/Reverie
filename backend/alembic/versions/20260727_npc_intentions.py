"""An NPC's plans outlive the turn that formed them.

NPCDecisionService derived follow-ups on an escalating ladder (watch → question →
search → move valuables → call help → block the exit) and threw them away every turn.
So attitudes changed numerically without changing behaviour — the numbers persisted in
npc_relationships, the behaviour they implied did not — and because the path only ran
when an NPC was spoken TO, an NPC could never act while the party was elsewhere.

The unique constraint is what stops "watch them closely" stacking six copies as the
same plan is re-derived each turn; it is scoped to `state` so a FULFILLED intention
does not block the NPC ever deciding the same thing again later.

New table only — nothing existing changes behaviour until intentions are written.

Revision ID: 20260727_npc_intentions
Revises: 20260726_scene_pacing
"""
from alembic import op
import sqlalchemy as sa

revision = "20260727_npc_intentions"
down_revision = "20260726_scene_pacing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "npc_intentions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("npc_id", sa.String(32), sa.ForeignKey("npcs.id"), nullable=False),
        sa.Column("subject_ref", sa.String(80), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("trigger", sa.String(20), nullable=False, server_default="ON_NEXT_MEETING"),
        sa.Column("trigger_game_time", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("urgency", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("source_memory_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("npc_id", "subject_ref", "kind", "state",
                            name="uq_npc_intention_live"),
    )
    op.create_index("ix_npc_intentions_npc_subject", "npc_intentions",
                    ["npc_id", "subject_ref"])
    op.create_index("ix_npc_intentions_due", "npc_intentions",
                    ["state", "trigger", "trigger_game_time"])
    op.create_index("ix_npc_intentions_state", "npc_intentions", ["state"])


def downgrade() -> None:
    op.drop_index("ix_npc_intentions_state", table_name="npc_intentions")
    op.drop_index("ix_npc_intentions_due", table_name="npc_intentions")
    op.drop_index("ix_npc_intentions_npc_subject", table_name="npc_intentions")
    op.drop_table("npc_intentions")
