"""Wallet + CurrencyTransaction — authoritative money (E7 §15).

Balances change only through WalletService inside a unit-of-work, each change
paired with a CurrencyTransaction row (the audit ledger). Narration never
"succeeds" a purchase the ledger didn't commit.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column


class Wallet(Base, TimestampMixin):
    __tablename__ = "wallets"
    __table_args__ = (UniqueConstraint("character_id", name="uq_wallet_character"),)

    id: Mapped[str] = pk_column()
    character_id: Mapped[str] = fk_id("characters.id")
    # denomination -> amount, e.g. {"gp": 12, "sp": 4}. Non-negative unless the
    # campaign explicitly allowed debt for that transaction.
    balances: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)


class CurrencyTransaction(Base, TimestampMixin):
    __tablename__ = "currency_transactions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_currency_tx_idempotency"),
        Index("ix_currency_tx_campaign", "campaign_id"),
    )

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    # entity refs ("character:<id>", "npc:<id>", "shop:<id>", "world").
    actor_ref: Mapped[str] = mapped_column(String(64))
    counterparty_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Signed amounts from the ACTOR's perspective: {"gp": -5} means the actor paid 5 gp.
    amounts: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    transaction_type: Mapped[str] = mapped_column(String(30))  # GRANT|SPEND|TRANSFER|LOOT|REWARD|FINE|THEFT
    item_refs: Mapped[list[Any]] = mapped_column(JSON, default=list)
    game_time: Mapped[int] = mapped_column(Integer, default=0)
    source_event_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Discord retries / double taps commit at most once.
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reason: Mapped[str] = mapped_column(String(400), default="")
