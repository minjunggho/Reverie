"""Idempotency + per-action processing state, keyed by discord_message_id.

`get_or_create` is the dedupe gate: the first time a Discord message id is seen it is
inserted at stage RECEIVED and `created=True`; every redelivery returns the existing
row with `created=False`, so the bridge can resume from the recorded stage instead of
re-executing (see error-recovery §32).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import MessageCategory, ProcessingStage
from app.models.processed_message import ProcessedMessage


class ProcessedMessageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _reset_session(self) -> None:
        try:
            await self.session.rollback()
        except Exception:  # noqa: BLE001 - keep recovery tolerant
            pass
        self.session.expire_all()

    async def get(self, discord_message_id: str) -> ProcessedMessage | None:
        try:
            return (
                await self.session.execute(
                    select(ProcessedMessage).where(
                        ProcessedMessage.discord_message_id == discord_message_id
                    )
                )
            ).scalar_one_or_none()
        except PendingRollbackError:
            await self._reset_session()
            return (
                await self.session.execute(
                    select(ProcessedMessage).where(
                        ProcessedMessage.discord_message_id == discord_message_id
                    )
                )
            ).scalar_one_or_none()

    async def get_or_create(
        self,
        *,
        discord_message_id: str,
        campaign_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[ProcessedMessage, bool]:
        existing = await self.get(discord_message_id)
        if existing is not None:
            return existing, False
        pm = ProcessedMessage(
            discord_message_id=discord_message_id,
            campaign_id=campaign_id,
            session_id=session_id,
            stage=ProcessingStage.RECEIVED.value,
        )
        self.session.add(pm)
        try:
            await self.session.flush()
        except IntegrityError:
            await self._reset_session()
            existing = await self.get(discord_message_id)
            if existing is not None:
                return existing, False
            raise
        return pm, True

    async def claim_once(
        self,
        *,
        discord_message_id: str,
        campaign_id: str | None = None,
        session_id: str | None = None,
    ) -> bool:
        """Atomically claim a Discord message across workers.

        This is an at-most-once dispatch gate, unlike ``get_or_create`` whose
        caller may need the existing row to resume engine work.  SQLite and
        PostgreSQL both use their native conflict handling so simultaneous bot
        instances cannot win a select-then-insert race.
        """
        values = {
            "discord_message_id": discord_message_id,
            "campaign_id": campaign_id,
            "session_id": session_id,
            "stage": ProcessingStage.RECEIVED.value,
            "result": {},
        }
        bind = self.session.get_bind()
        dialect = bind.dialect.name
        if dialect == "sqlite":
            statement = sqlite_insert(ProcessedMessage).values(**values)
            statement = statement.on_conflict_do_nothing(
                index_elements=[ProcessedMessage.discord_message_id]
            )
            result = await self.session.execute(statement)
            return result.rowcount == 1
        if dialect == "postgresql":
            statement = postgresql_insert(ProcessedMessage).values(**values)
            statement = statement.on_conflict_do_nothing(
                index_elements=[ProcessedMessage.discord_message_id]
            )
            result = await self.session.execute(statement)
            return result.rowcount == 1

        # Portable fallback for an unsupported SQLAlchemy dialect.  The nested
        # transaction contains a unique-key race without invalidating the outer
        # unit of work.
        try:
            async with self.session.begin_nested():
                self.session.add(ProcessedMessage(**values))
                await self.session.flush()
        except IntegrityError:
            return False
        return True

    async def advance_stage(self, pm: ProcessedMessage, stage: ProcessingStage) -> ProcessedMessage:
        pm.stage = stage.value
        return pm

    async def set_category(self, pm: ProcessedMessage, category: MessageCategory) -> ProcessedMessage:
        pm.category = category.value
        return pm

    async def set_result(self, pm: ProcessedMessage, result: dict[str, Any]) -> ProcessedMessage:
        pm.result = result
        return pm

    async def set_pending_action(self, pm: ProcessedMessage, action_id: str | None) -> ProcessedMessage:
        pm.pending_action_id = action_id
        return pm
