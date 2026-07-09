"""Phase 1 acceptance: app boots, DB works, test harness (FakeLLM + deterministic
dice) works."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.config import Settings
from app.core.errors import LLMError
from app.core.randomness import SequenceRandomness, SystemRandomness
from app.main import app
from app.schemas.llm_io import ClassificationResult


async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_settings_default_to_fake_provider():
    s = Settings(_env_file=None)
    assert s.llm_provider == "fake"
    assert s.is_sqlite  # default database url is sqlite


async def test_database_create_all_and_query(db):
    async with db.session() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1


async def test_fake_provider_returns_schema_valid_object(provider):
    messages = [{"role": "user", "content": "MESSAGE: สวัสดีครับทุกคน"}]
    result = await provider.classify_table_message(messages)
    assert isinstance(result, ClassificationResult)
    assert 0.0 <= result.confidence <= 1.0


async def test_fake_provider_raises_without_script():
    from app.ai.llm.fake import FakeLLMProvider

    bare = FakeLLMProvider()
    with pytest.raises(LLMError):
        await bare.classify_table_message([{"role": "user", "content": "hi"}])


def test_sequence_randomness_is_deterministic():
    rng = SequenceRandomness([14, 3, 20])
    assert rng.roll(20) == 14
    assert rng.roll(20) == 3
    assert rng.roll(20) == 20


def test_sequence_randomness_validates_face_range():
    rng = SequenceRandomness([25])
    with pytest.raises(ValueError):
        rng.roll(20)  # 25 is not a valid d20 face


def test_system_randomness_in_range():
    rng = SystemRandomness(seed=1)
    for _ in range(50):
        assert 1 <= rng.roll(20) <= 20
