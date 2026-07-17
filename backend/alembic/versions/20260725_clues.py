"""Clues become entities with edges, instead of free text in three places.

A clue was `list[str]` on Secret, on Scene.allowed_clues, and in main_story["leads"] —
prose, linked to nothing. So the engine could not know that the torn ledger page is
what opens the route under the harbour: no field existed that could hold the edge, and
a revealed clue was narrated and forgotten.

`clues.reveals` is that edge: [{"kind": "location"|"route"|"objective"|"fact"|..., "ref": ...}].

Purely additive — nothing reads the old string lists differently, so existing campaigns
keep working exactly as they did until they are re-imported with clue blocks.

Revision ID: 20260725_clues
Revises: 20260724_campaign_progression
"""
from alembic import op
import sqlalchemy as sa

revision = "20260725_clues"
down_revision = "20260724_campaign_progression"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clues",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("campaign_id", sa.String(32), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("location_id", sa.String(32), nullable=True),
        sa.Column("npc_id", sa.String(32), nullable=True),
        sa.Column("secret_id", sa.String(32), nullable=True),
        sa.Column("reveals", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("discovered", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("discovered_game_time", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("importance", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("campaign_id", "key", name="uq_clue_campaign_key"),
    )
    op.create_index("ix_clues_location_id", "clues", ["location_id"])
    op.create_index("ix_clues_npc_id", "clues", ["npc_id"])
    op.create_index("ix_clues_secret_id", "clues", ["secret_id"])
    op.create_index("ix_clues_discovered", "clues", ["discovered"])


def downgrade() -> None:
    op.drop_index("ix_clues_discovered", table_name="clues")
    op.drop_index("ix_clues_secret_id", table_name="clues")
    op.drop_index("ix_clues_npc_id", table_name="clues")
    op.drop_index("ix_clues_location_id", table_name="clues")
    op.drop_table("clues")
