"""MessageRouter — handles non-committed (normal) messages.

Normal messages are discussion, questions, dialogue, jokes. The router classifies
them and responds only when appropriate. It NEVER mutates game state — with one
principled exception: CHARACTER_DIALOGUE addressed at a visible NPC routes to the
social service, whose belief/attitude deltas are AI-proposed and engine-committed
(Phase 11 rules; epistemic-scoped, DM-visible only).

Operational bookkeeping (category + cached response on ProcessedMessage) is not a
game event.
"""
from __future__ import annotations

from app.ai.jobs.classifier import TableMessageClassifier
from app.ai.llm.base import LLMProvider
from app.core.ids import entity_ref, parse_entity_ref
from app.discord_bridge.dto import BridgeResult, OutboundMessage
from app.models.character import Character
from app.models.enums import MessageCategory, ProcessingStage
from app.models.npc import NPC
from app.models.processed_message import ProcessedMessage
from app.orchestration.context import ResolvedContext
from app.presentation import MessageKind
from app.services.messages import ProcessedMessageService
from app.services.scenes import SceneService

# Categories the bot answers out loud. Others (OOC, jokes) it lets pass.
_ANSWERABLE = {MessageCategory.DM_QUESTION, MessageCategory.RULES_QUESTION}

_FALLBACK_ANSWER = "รับทราบ เดี๋ยว DM ช่วยดูให้นะ"


class MessageRouter:
    def __init__(self, db, provider: LLMProvider) -> None:
        self.db = db
        self.provider = provider
        self.classifier = TableMessageClassifier(provider)

    async def handle(self, ctx: ResolvedContext) -> BridgeResult:
        # 1. classify (read-only). Speaker identity is preserved: normal dialogue
        #    belongs to the SENDER's character, not to unattributed table talk.
        async with self.db.session() as read:
            scene = (
                await SceneService(read).get_active_scene(ctx.session_id)
                if ctx.session_id else None
            )
            speaker = await read.get(Character, ctx.character_id) if ctx.character_id else None
            speaker_name = speaker.name if speaker is not None else None
            result = await self.classifier.run(
                read, message_text=ctx.inbound.content, scene=scene,
                speaker_name=speaker_name,
            )
            npc_target = self._visible_npc(scene) if scene is not None else None
            if npc_target is not None:
                npc_row = await read.get(NPC, npc_target[0])
                npc_target = (npc_target[0], npc_row.name if npc_row else "ใครบางคน")

        responses: list[OutboundMessage] = []

        # 2. in-character dialogue at a visible NPC -> the NPC answers from its
        #    OWN knowledge (epistemic-scoped; deltas engine-committed).
        if (
            result.category == MessageCategory.CHARACTER_DIALOGUE
            and npc_target is not None
            and ctx.character_id is not None
        ):
            from app.npcs import NPCSocialService

            social = await NPCSocialService(self.db, self.provider).respond(
                campaign_id=ctx.campaign_id, npc_id=npc_target[0],
                listener_ref=entity_ref("character", ctx.character_id),
                utterance=ctx.inbound.content, session_id=ctx.session_id,
            )
            responses.append(OutboundMessage(
                ctx.channel_id, social.utterance, kind=MessageKind.NPC_DIALOGUE,
                title=npc_target[1],
            ))
        elif result.category in _ANSWERABLE:
            answer = result.suggested_response or _FALLBACK_ANSWER
            responses.append(OutboundMessage(ctx.channel_id, answer,
                                             kind=MessageKind.TABLE_NOTICE))

        # 3. operational bookkeeping only — NOT a game event. Speaker identity is
        #    retained on the record so later scene context knows who spoke.
        speaker_ref = entity_ref("character", ctx.character_id) if ctx.character_id else None
        async with self.db.unit_of_work() as s:
            if ctx.processed_message_id:
                pm = await s.get(ProcessedMessage, ctx.processed_message_id)
                if pm is not None:
                    svc = ProcessedMessageService(s)
                    await svc.set_category(pm, result.category)
                    res: dict = {"speaker": speaker_ref, "speaker_name": speaker_name}
                    if responses:
                        res["response"] = responses[0].content
                    await svc.set_result(pm, res)
                    await svc.advance_stage(pm, ProcessingStage.SENT)

        return BridgeResult(
            handled=True,
            category=result.category,
            responses=responses,
            state_mutated=False,
            note=f"non-committed; speaker={speaker_name or '-'}; no canonical state change",
        )

    @staticmethod
    def _visible_npc(scene) -> tuple[str, str] | None:
        """(npc_id, name placeholder) for the first visible NPC, if any."""
        for ref in list(scene.visible_entity_ids or []):
            kind, npc_id = parse_entity_ref(ref)
            if kind == "npc" and npc_id:
                return npc_id, "NPC"
        return None
