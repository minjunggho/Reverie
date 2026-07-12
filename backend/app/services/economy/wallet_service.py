"""WalletService — the only sanctioned mutator of character money (E7 §15).

Every change happens inside the caller's unit-of-work and writes a
CurrencyTransaction ledger row. Balances can never go negative unless the caller
explicitly allows debt (an agreed loan), and an idempotency key makes Discord
retries commit at most once. Narration must describe what the ledger committed —
never the other way around.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.ids import entity_ref
from app.models.campaign import Campaign
from app.models.character import Character
from app.models.economy import CurrencyTransaction, Wallet

# Starting purse per class (gp) — modest SRD-flavored defaults; campaign-specific
# economies can override at grant time.
STARTING_FUNDS: dict[str, dict[str, int]] = {
    "fighter": {"gp": 12}, "rogue": {"gp": 10}, "wizard": {"gp": 8},
    "cleric": {"gp": 10}, "ranger": {"gp": 10}, "bard": {"gp": 12},
    "barbarian": {"gp": 8}, "druid": {"gp": 8}, "monk": {"gp": 5},
    "paladin": {"gp": 12}, "sorcerer": {"gp": 8}, "warlock": {"gp": 10},
}
DEFAULT_FUNDS: dict[str, int] = {"gp": 10}

# Thai display names for denominations.
DENOM_TH: dict[str, str] = {"gp": "เหรียญทอง", "sp": "เหรียญเงิน", "cp": "เหรียญทองแดง"}


def format_balances(balances: dict[str, int]) -> str:
    if not any(balances.values()):
        return "ถุงเงินว่างเปล่า"
    parts = [f"{v} {DENOM_TH.get(k, k)}" for k, v in balances.items() if v]
    return " · ".join(parts)


class WalletService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_wallet(self, character_id: str) -> Wallet:
        wallet = (await self.session.execute(
            select(Wallet).where(Wallet.character_id == character_id)
        )).scalars().first()
        if wallet is None:
            character = await self.session.get(Character, character_id)
            if character is None:
                raise NotFoundError(f"character {character_id} not found")
            wallet = Wallet(character_id=character_id, balances={})
            self.session.add(wallet)
            await self.session.flush()
        return wallet

    async def balance(self, character_id: str) -> dict[str, int]:
        return dict((await self.get_wallet(character_id)).balances or {})

    async def apply(
        self, *, character_id: str, amounts: dict[str, int], transaction_type: str,
        counterparty_ref: str | None = None, reason: str = "",
        item_refs: list | None = None, source_event_id: str | None = None,
        idempotency_key: str | None = None, allow_debt: bool = False,
    ) -> dict[str, int]:
        """Apply signed `amounts` to the character's wallet atomically and write the
        ledger row. Returns the new balances. Raises ValidationError when funds are
        insufficient (unless debt was explicitly agreed) and ConflictError when the
        idempotency key was already committed."""
        if not amounts or all(v == 0 for v in amounts.values()):
            raise ValidationError("transaction must move at least one coin")
        character = await self.session.get(Character, character_id)
        if character is None:
            raise NotFoundError(f"character {character_id} not found")
        if idempotency_key:
            dup = (await self.session.execute(
                select(CurrencyTransaction).where(
                    CurrencyTransaction.idempotency_key == idempotency_key)
            )).scalars().first()
            if dup is not None:
                raise ConflictError("this transaction was already committed")

        wallet = await self.get_wallet(character_id)
        new_balances = dict(wallet.balances or {})
        for denom, delta in amounts.items():
            new_balances[denom] = new_balances.get(denom, 0) + int(delta)
            if new_balances[denom] < 0 and not allow_debt:
                raise ValidationError(
                    f"เงินไม่พอ — ต้องใช้ {-int(delta)} {DENOM_TH.get(denom, denom)} "
                    f"แต่มี {(wallet.balances or {}).get(denom, 0)}")
        wallet.balances = new_balances

        campaign = await self.session.get(Campaign, character.campaign_id)
        self.session.add(CurrencyTransaction(
            campaign_id=character.campaign_id,
            actor_ref=entity_ref("character", character_id),
            counterparty_ref=counterparty_ref,
            amounts=dict(amounts), transaction_type=transaction_type,
            item_refs=list(item_refs or []),
            game_time=campaign.current_game_time if campaign else 0,
            source_event_id=source_event_id,
            idempotency_key=idempotency_key, reason=reason,
        ))
        await self.session.flush()
        return new_balances

    async def transfer(
        self, *, from_character_id: str, to_character_id: str,
        amounts: dict[str, int], reason: str = "",
        idempotency_key: str | None = None,
    ) -> None:
        """Move positive `amounts` between two characters atomically (one UoW)."""
        if any(v <= 0 for v in amounts.values()):
            raise ValidationError("transfer amounts must be positive")
        await self.apply(
            character_id=from_character_id,
            amounts={k: -v for k, v in amounts.items()},
            transaction_type="TRANSFER",
            counterparty_ref=entity_ref("character", to_character_id),
            reason=reason,
            idempotency_key=f"{idempotency_key}:out" if idempotency_key else None,
        )
        await self.apply(
            character_id=to_character_id, amounts=dict(amounts),
            transaction_type="TRANSFER",
            counterparty_ref=entity_ref("character", from_character_id),
            reason=reason,
            idempotency_key=f"{idempotency_key}:in" if idempotency_key else None,
        )

    async def grant_starting_funds(self, *, character: Character) -> dict[str, int]:
        """Class-appropriate starting purse at character creation. Idempotent per
        character (setup, not play — mirrors grant_starting_gear)."""
        existing = (await self.session.execute(
            select(CurrencyTransaction).where(
                CurrencyTransaction.idempotency_key == f"starting-funds:{character.id}")
        )).scalars().first()
        if existing is not None:
            return await self.balance(character.id)
        funds = STARTING_FUNDS.get(character.char_class, DEFAULT_FUNDS)
        return await self.apply(
            character_id=character.id, amounts=dict(funds),
            transaction_type="GRANT", reason="ทุนเริ่มต้นของตัวละคร",
            idempotency_key=f"starting-funds:{character.id}",
        )

    async def recent_transactions(self, character_id: str, limit: int = 5
                                  ) -> list[CurrencyTransaction]:
        ref = entity_ref("character", character_id)
        rows = (await self.session.execute(
            select(CurrencyTransaction)
            .where((CurrencyTransaction.actor_ref == ref)
                   | (CurrencyTransaction.counterparty_ref == ref))
            .order_by(CurrencyTransaction.created_at.desc())
            .limit(limit)
        )).scalars().all()
        return list(rows)
