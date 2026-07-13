"""Regression coverage for visible duplicate ``!rv help`` replies."""
from __future__ import annotations

import asyncio

import pytest

from app.db.session import Database
from tests.test_production_discord_callbacks import (
    FakeAuthor,
    FakeChannel,
    FakeMessage,
    LiveTable,
)


pytestmark = pytest.mark.asyncio


def _help_message(channel: FakeChannel) -> FakeMessage:
    return FakeMessage(
        content="!rv help",
        author=FakeAuthor(user_id="discord-help-user", display_name="Help User"),
        channel=channel,
    )


def _assert_one_visible_help(channel: FakeChannel) -> None:
    assert len(channel.sent) == 1
    assert channel.sent[0]["embed"] is not None
    assert (channel.sent[0]["embed"].title or "").endswith("Reverie")


async def test_same_help_message_replay_sends_one_visible_reply(db, provider):
    table = LiveTable(db, provider)
    channel = FakeChannel()
    message = _help_message(channel)

    await table.client.on_message(message)
    await table.client.on_message(message)

    _assert_one_visible_help(channel)


async def test_two_client_instances_sharing_database_send_one_visible_help(
    tmp_path, provider
):
    path = (tmp_path / "shared-help-idempotency.db").as_posix()
    url = f"sqlite+aiosqlite:///{path}"
    first_database = Database(url, echo=False)
    second_database = Database(url, echo=False)
    await first_database.create_all()
    try:
        first = LiveTable(first_database, provider)
        second = LiveTable(second_database, provider)
        assert first.client is not second.client
        assert first.admin is not second.admin

        channel = FakeChannel()
        message = _help_message(channel)
        await asyncio.gather(
            first.client.on_message(message),
            second.client.on_message(message),
        )

        _assert_one_visible_help(channel)
    finally:
        await second_database.dispose()
        await first_database.dispose()
