"""Wallets + currency transactions (authoritative money, E7 §15).

Revision ID: 20260711_economy
Revises: 20260711_anchors
"""
from alembic import op
import sqlalchemy as sa

revision = "20260711_economy"
down_revision = "20260711_anchors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("character_id", sa.String(32), sa.ForeignKey("characters.id"), nullable=False),
        sa.Column("balances", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("character_id", name="uq_wallet_character"),
    )
    op.create_table(
        "currency_transactions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("campaign_id", sa.String(32), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("actor_ref", sa.String(64), nullable=False),
        sa.Column("counterparty_ref", sa.String(64), nullable=True),
        sa.Column("amounts", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("transaction_type", sa.String(30), nullable=False),
        sa.Column("item_refs", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("game_time", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_event_id", sa.String(32), nullable=True),
        sa.Column("idempotency_key", sa.String(80), nullable=True),
        sa.Column("reason", sa.String(400), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_currency_tx_idempotency"),
    )
    op.create_index("ix_currency_tx_campaign", "currency_transactions", ["campaign_id"])


def downgrade() -> None:
    op.drop_index("ix_currency_tx_campaign", table_name="currency_transactions")
    op.drop_table("currency_transactions")
    op.drop_table("wallets")
