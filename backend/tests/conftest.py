"""Pytest fixtures: an isolated in-memory database, a deterministic RNG, and a
scripted FakeLLMProvider. No external services are touched.
"""
from __future__ import annotations

import pytest_asyncio

from app.ai.llm.fake import FakeLLMProvider
from app.core.randomness import SequenceRandomness
from app.db.session import Database
from tests.support.fake_script import install_default_script


@pytest_asyncio.fixture
async def db() -> Database:
    """A fresh in-memory SQLite database with the full schema, per test."""
    database = Database("sqlite+aiosqlite:///:memory:", echo=False)
    await database.create_all()
    try:
        yield database
    finally:
        await database.dispose()


@pytest_asyncio.fixture
def rng() -> SequenceRandomness:
    """Deterministic dice. Tests push exact faces, e.g. rng.push(14)."""
    return SequenceRandomness()


@pytest_asyncio.fixture
def provider() -> FakeLLMProvider:
    """FakeLLMProvider preloaded with the default scenario script.

    Individual tests override any task with `provider.on(task, ...)` or
    `provider.push(task, ...)`.
    """
    fake = FakeLLMProvider()
    install_default_script(fake)
    return fake
