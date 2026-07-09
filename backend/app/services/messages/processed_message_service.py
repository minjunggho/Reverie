"""Idempotency + per-action processing state, keyed by discord_message_id.

`get_or_create` is the dedupe gate: the first time a Discord message id is seen it is
inserted at stage RECEIVED and `created=True`; every redelivery returns the existing
row with `created=False`, so the bridge can resume from the recorded stage instead of
re-executing (see error-recovery §32).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import MessageCategory, ProcessingStage
from app.models.processed_message import ProcessedMessage


class ProcessedMessageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, discord_message_id: str) -> ProcessedMessage | None:
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
        await self.session.flush()
        return pm, True

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
