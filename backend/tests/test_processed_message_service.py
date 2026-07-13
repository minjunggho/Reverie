from __future__ import annotations

from app.models.processed_message import ProcessedMessage
from app.services.messages.processed_message_service import ProcessedMessageService


async def test_get_or_create_returns_existing_row_on_duplicate_insert(db):
    async with db.session() as session:
        service = ProcessedMessageService(session)
        pm, created = await service.get_or_create(discord_message_id="msg-1")
        assert created is True
        assert pm.discord_message_id == "msg-1"

        duplicate, created_again = await service.get_or_create(discord_message_id="msg-1")
        assert created_again is False
        assert duplicate.id == pm.id

        # Simulate a race where another insert slips in between the existence check
        # and the flush by creating a second row through the session directly.
        session.add(ProcessedMessage(discord_message_id="msg-1"))
        try:
            await session.flush()
        except Exception:
            pass

        # The service should recover by reloading the existing row without crashing.
        recovered, created_third = await service.get_or_create(discord_message_id="msg-1")
        assert recovered.discord_message_id == "msg-1"
