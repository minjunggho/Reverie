"""Startup identity, credential redaction, and host-local bot ownership."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.logging import redact_database_url
from discord_bot.client import BotInstanceInfo, ReverieClient
from discord_bot.instance_lock import BotInstanceLock, DuplicateBotInstanceError


@pytest.mark.parametrize(
    ("raw", "expected", "secrets"),
    [
        (
            "postgresql+asyncpg://db_user:p%40ssword@db.example:5432/reverie"
            "?ssl=require&access_token=query-secret",
            "postgresql+asyncpg://db.example:5432/reverie",
            ("db_user", "p%40ssword", "p@ssword", "query-secret", "access_token"),
        ),
        (
            "postgresql+asyncpg://username-is-token@db.internal/reverie"
            "?password=query-password",
            "postgresql+asyncpg://db.internal/reverie",
            ("username-is-token", "query-password", "password="),
        ),
        (
            "sqlite+aiosqlite:///./reverie_dev.sqlite3?auth_token=local-secret",
            "sqlite+aiosqlite:///./reverie_dev.sqlite3",
            ("auth_token", "local-secret"),
        ),
    ],
)
def test_database_url_redaction_removes_credentials_and_query(
    raw: str,
    expected: str,
    secrets: tuple[str, ...],
):
    redacted = redact_database_url(raw)
    assert redacted == expected
    assert "?" not in redacted
    for secret in secrets:
        assert secret not in redacted


def test_same_bot_token_is_exclusive_until_owner_releases(tmp_path):
    token = "discord-token-that-must-never-be-written"
    owner = BotInstanceLock(token, directory=tmp_path)
    contender = BotInstanceLock(token, directory=tmp_path)

    owner.acquire()
    try:
        with pytest.raises(DuplicateBotInstanceError, match="already using this token"):
            contender.acquire()
    finally:
        owner.release()

    # Releasing the first owner makes the same token available immediately.
    contender.acquire()
    contender.release()

    assert owner.path == contender.path
    assert token not in owner.path.name
    assert token.encode("utf-8") not in owner.path.read_bytes()


def test_different_bot_tokens_can_be_locked_together_without_writing_tokens(tmp_path):
    first_token = "discord-token-one"
    second_token = "discord-token-two"
    first = BotInstanceLock(first_token, directory=tmp_path)
    second = BotInstanceLock(second_token, directory=tmp_path)

    first.acquire()
    second.acquire()
    try:
        assert first.path != second.path
        assert first.path.exists() and second.path.exists()
    finally:
        second.release()
        first.release()

    # Windows does not allow reopening the locked byte for reading, so inspect
    # persisted contents only after proving both locks coexist and releasing them.
    for lock, token in ((first, first_token), (second, second_token)):
        assert token not in lock.path.name
        contents = lock.path.read_bytes()
        assert contents == b"\0"
        assert token.encode("utf-8") not in contents


@pytest.mark.asyncio
async def test_on_ready_logs_one_full_identity_then_reconnect_without_secrets(caplog):
    raw_database_url = (
        "postgresql+asyncpg://private-user:private-password@db.example:5432/reverie"
        "?access_token=private-query-token"
    )
    info = BotInstanceInfo(
        pid=4321,
        hostname="reverie-host",
        instance_id="instance-abc123",
        git_sha="deadbee",
        database_url=redact_database_url(raw_database_url),
        process_started_at="2026-07-13T12:00:00+00:00",
    )
    client = ReverieClient(object(), object(), instance_info=info)
    client._connection.user = SimpleNamespace(id=987654321, name="Reverie")

    with caplog.at_level(logging.INFO, logger="discord_bot.client"):
        await client.on_ready()
        await client.on_ready()

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "discord_bot.client"
    ]
    startup = [message for message in messages if message.startswith("Bot instance started ")]
    reconnects = [
        message for message in messages
        if message.startswith("Discord gateway reconnected ")
    ]
    assert len(startup) == 1
    assert len(reconnects) == 1

    payload = json.loads(startup[0].removeprefix("Bot instance started "))
    assert payload == {
        "bot_user_id": "987654321",
        "database_url": "postgresql+asyncpg://db.example:5432/reverie",
        "event": "bot_instance_started",
        "git_sha": "deadbee",
        "hostname": "reverie-host",
        "instance_id": "instance-abc123",
        "pid": 4321,
        "process_started_at": "2026-07-13T12:00:00+00:00",
        "timestamp": payload["timestamp"],
    }
    timestamp = datetime.fromisoformat(payload["timestamp"])
    assert timestamp.utcoffset() == timedelta(0)
    assert "instance=instance-abc123" in reconnects[0]
    assert "bot_user=987654321" in reconnects[0]

    rendered = "\n".join(messages)
    for secret in (
        "private-user",
        "private-password",
        "private-query-token",
        "access_token",
        "discord-bot-token",
    ):
        assert secret not in rendered
