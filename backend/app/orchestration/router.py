"""MessageRouter — handles non-committed (normal) messages.

Normal messages are discussion, questions, dialogue, jokes. The router classifies
them and responds only when appropriate. It NEVER mutates game state and NEVER emits
canonical events. The only write it makes is operational: recording the category and
any cached response on the ProcessedMessage row for idempotency.
"""
from __future__ import annotations

from app.ai.jobs.classifier import TableMessageClassifier
from app.ai.llm.base import LLMProvider
from app.discord_bridge.dto import BridgeResult, OutboundMessage
from app.models.enums import MessageCategory, ProcessingStage
from app.models.processed_message import ProcessedMessage
from app.orchestration.context import ResolvedContext
from app.services.messages import ProcessedMessageService
from app.services.scenes import SceneService

# Categories the bot answers out loud. Others (dialogue, OOC, jokes) it lets pass.
_ANSWERABLE = {MessageCategory.DM_QUESTION, MessageCategory.RULES_QUESTION}

_FALLBACK_ANSWER = "รับทราบ เดี๋ยว DM ช่วยดูให้นะ"


class MessageRouter:
    def __init__(self, db, provider: LLMProvider) -> None:
        self.db = db
        self.classifier = TableMessageClassifier(provider)

    async def handle(self, ctx: ResolvedContext) -> BridgeResult:
        async with self.db.unit_of_work() as session:
            scene = (
                await SceneService(session).get_active_scene(ctx.session_id)
                if ctx.session_id
                else None
            )
            result = await self.classifier.run(
                session, message_text=ctx.inbound.content, scene=scene
            )

            responses: list[OutboundMessage] = []
            if result.category in _ANSWERABLE:
                answer = result.suggested_response or _FALLBACK_ANSWER
                responses.append(OutboundMessage(ctx.channel_id, answer))

            # Operational bookkeeping only — NOT a game event, NO state mutation.
            pm_service = ProcessedMessageService(session)
            if ctx.processed_message_id:
                pm = await session.get(ProcessedMessage, ctx.processed_message_id)
                if pm is not None:
                    await pm_service.set_category(pm, result.category)
                    await pm_service.set_result(
                        pm, {"response": responses[0].content} if responses else {}
                    )
                    await pm_service.advance_stage(pm, ProcessingStage.SENT)

        return BridgeResult(
            handled=True,
            category=result.category,
            responses=responses,
            state_mutated=False,
            note="non-committed message; no state change",
        )
