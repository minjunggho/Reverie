"""TableMessageClassifier — classify a non-`!` message. Mutates NO state.

FORBIDDEN: any state mutation. FALLBACK: UNKNOWN -> minimal safe response.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMProvider
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.memory.context_builders import build_classification_context
from app.models.enums import MessageCategory
from app.models.scene import Scene
from app.schemas.llm_io import ClassificationResult

log = get_logger(__name__)


class TableMessageClassifier:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def run(
        self, session: AsyncSession, *, message_text: str, scene: Scene | None,
        speaker_name: str | None = None,
    ) -> ClassificationResult:
        messages = await build_classification_context(
            session, message_text=message_text, scene=scene, speaker_name=speaker_name,
        )
        try:
            return await self.provider.classify_table_message(messages)
        except LLMError as exc:  # bounded retries already exhausted inside provider
            log.warning("classifier fell back to UNKNOWN: %s", exc)
            return ClassificationResult(category=MessageCategory.UNKNOWN, confidence=0.0)
