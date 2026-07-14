"""Persistent world consequences: factions, reputations, crime records, quests, and
rumors — plus durable NPC physical condition + availability.

Player actions now leave marks the world remembers across sessions and restart. New
tables are additive; the two new NPC columns carry safe defaults so every existing NPC
is ``healthy`` and ``available`` until something changes that.

Revision ID: 20260721_consequences
Revises: 20260720_npc_biases
"""
from alembic import op
import sqlalchemy as sa

revision = "20260721_consequences"
down_revision = "20260720_npc_biases"
branch_labels = None
depends_on = None


def _campaign_fk() -> sa.ForeignKey:
    return sa.ForeignKey("campaigns.id", ondelete="CASCADE")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "factions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("campaign_id", sa.String(32), _campaign_fk(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("leader_ref", sa.String(80), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disposition_to_party", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resources", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("territory", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("relationships", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("knowledge", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("plans", sa.Text(), nullable=False, server_default=""),
        sa.Column("scheduled_game_time", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
    )

    op.create_table(
        "reputations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("campaign_id", sa.String(32), _campaign_fk(), nullable=False),
        sa.Column("subject_ref", sa.String(80), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False, server_default="LOCAL"),
        sa.Column("scope_ref", sa.String(80), nullable=True),
        sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wanted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        *_timestamps(),
    )

    op.create_table(
        "crime_records",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("campaign_id", sa.String(32), _campaign_fk(), nullable=False),
        sa.Column("crime_type", sa.String(40), nullable=False),
        sa.Column("victim_ref", sa.String(80), nullable=True),
        sa.Column("perpetrator_ref", sa.String(80), nullable=True),
        sa.Column("location_id", sa.String(32), nullable=True),
        sa.Column("game_time", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("perceived", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("identified", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("reported", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("witnesses", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("source_event_id", sa.String(32), nullable=True),
        *_timestamps(),
    )
    op.create_index(
        "ix_crime_records_source_event_id", "crime_records", ["source_event_id"]
    )

    op.create_table(
        "quests",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("campaign_id", sa.String(32), _campaign_fk(), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("state", sa.String(20), nullable=False, server_default="UNKNOWN"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        *_timestamps(),
    )

    op.create_table(
        "rumors",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("campaign_id", sa.String(32), _campaign_fk(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("truth", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("origin_location_id", sa.String(32), nullable=True),
        sa.Column("spread_stage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("known_scope", sa.String(20), nullable=False, server_default="LOCAL"),
        sa.Column("source_event_id", sa.String(32), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_rumors_source_event_id", "rumors", ["source_event_id"])

    # Durable NPC condition + availability. Existing NPCs are healthy and available.
    op.add_column("npcs", sa.Column(
        "physical_state", sa.String(40), nullable=False, server_default="healthy"))
    op.add_column("npcs", sa.Column(
        "available", sa.Boolean(), nullable=False, server_default=sa.text("1")))


def downgrade() -> None:
    op.drop_column("npcs", "available")
    op.drop_column("npcs", "physical_state")
    op.drop_index("ix_rumors_source_event_id", table_name="rumors")
    op.drop_table("rumors")
    op.drop_table("quests")
    op.drop_index("ix_crime_records_source_event_id", table_name="crime_records")
    op.drop_table("crime_records")
    op.drop_table("reputations")
    op.drop_table("factions")
