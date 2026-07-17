"""The objective layer: chapters, and quests that know their place in one.

A campaign had a goal (Campaign.central_question) and scenes had purposes, and there
was nothing in between — so nothing could answer "what is the party supposed to be
doing right now". Quest already had the state machine and the progress field but no
parent and no importer, so it was never created and never updated.

`chapters` is new. The `quests` columns are additive with safe defaults: an existing
quest gets chapter_id=NULL (free-floating, gating nothing), sort_order=0,
optional=False, task="" — its exact current meaning.

Revision ID: 20260724_campaign_progression
Revises: 20260723_action_memory
"""
from alembic import op
import sqlalchemy as sa

revision = "20260724_campaign_progression"
down_revision = "20260723_action_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chapters",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("campaign_id", sa.String(32), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("hidden_purpose", sa.Text(), nullable=False, server_default=""),
        sa.Column("optional", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("campaign_id", "key", name="uq_chapter_campaign_key"),
    )
    op.create_index("ix_chapters_state", "chapters", ["state"])

    op.add_column("quests", sa.Column("chapter_id", sa.String(32), nullable=True))
    op.add_column("quests", sa.Column(
        "sort_order", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("quests", sa.Column(
        "optional", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.add_column("quests", sa.Column(
        "task", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_quests_chapter_id", "quests", ["chapter_id"])


def downgrade() -> None:
    op.drop_index("ix_quests_chapter_id", table_name="quests")
    op.drop_column("quests", "task")
    op.drop_column("quests", "optional")
    op.drop_column("quests", "sort_order")
    op.drop_column("quests", "chapter_id")
    op.drop_index("ix_chapters_state", table_name="chapters")
    op.drop_table("chapters")
