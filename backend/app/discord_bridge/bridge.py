"""DiscordBridge — resolution, idempotency, serialization, and routing.

Pipeline (see §10):
  receive -> validate -> idempotency -> resolve campaign(by channel) -> resolve
  active session -> resolve member(by discord user) -> resolve active character ->
  detect `!` -> route (committed -> serialized pipeline; else -> classifier router).

The bridge itself contains NO game logic. Committed actions go through the
per-session serializer so they are handled one at a time in arrival order.
"""
from __future__ import annotations

from typing import Protocol

from app.core.logging import get_logger
from app.discord_bridge.dto import BridgeResult, InboundMessage, OutboundMessage
from app.models.enums import MessageCategory, ProcessingStage
from app.models.processed_message import ProcessedMessage
from app.orchestration.commitment import CommittedAction, detect_commitment
from app.orchestration.context import ResolvedContext
from app.orchestration.serializer import SessionSerializer
from app.presentation import MessageKind

log = get_logger(__name__)
from app.services.campaigns import CampaignService, CharacterService
from app.services.messages import ProcessedMessageService
from app.services.scenes import SceneService
from app.services.sessions.session_service import SessionService


class NonCommittedRouter(Protocol):
    async def handle(self, ctx: ResolvedContext) -> BridgeResult: ...


class CommittedPipeline(Protocol):
    async def handle(self, ctx: ResolvedContext, action: CommittedAction) -> BridgeResult: ...

    async def resume_clarification(
        self, ctx: ResolvedContext, *, answer_text: str, pending: dict
    ) -> BridgeResult: ...


class DiscordBridge:
    def __init__(
        self,
        db,
        *,
        router: NonCommittedRouter | None = None,
        pipeline: CommittedPipeline | None = None,
        serializer: SessionSerializer | None = None,
        creation_flow=None,  # CreationFlowService | None — guided character creation
        session_zero=None,   # SessionZeroService | None — table profile flow
    ) -> None:
        self.db = db
        self.router = router
        self.pipeline = pipeline
        self.serializer = serializer or SessionSerializer()
        self.creation_flow = creation_flow
        self.session_zero = session_zero

    async def handle_inbound(self, inbound: InboundMessage) -> BridgeResult:
        if inbound.is_bot:
            return BridgeResult(handled=False, note="ignored bot message")

        # --- idempotency + identity resolution (one transaction) ------------
        async with self.db.unit_of_work() as session:
            pm_service = ProcessedMessageService(session)
            existing = await pm_service.get(inbound.discord_message_id)
            if existing is not None:
                # Already seen: do NOT re-execute. Return any cached response.
                cached = (existing.result or {}).get("response")
                responses = (
                    [OutboundMessage(inbound.channel_id, cached)] if cached else []
                )
                category = (
                    MessageCategory(existing.category) if existing.category else None
                )
                return BridgeResult(
                    handled=True, duplicate=True, category=category,
                    responses=responses, note="duplicate discord_message_id",
                )

            camp_service = CampaignService(session)
            campaign = await camp_service.resolve_campaign_by_channel(inbound.channel_id)
            if campaign is None:
                return BridgeResult(handled=False, note="no campaign bound to channel")

            member = await camp_service.resolve_member(campaign.id, inbound.author_discord_id)
            active_session = await SessionService(session).get_active_session(campaign.id)

            pm, _ = await pm_service.get_or_create(
                discord_message_id=inbound.discord_message_id,
                campaign_id=campaign.id,
                session_id=active_session.id if active_session else None,
            )

            if member is None:
                await pm_service.set_result(pm, {"response": _NOT_A_MEMBER})
                await pm_service.advance_stage(pm, ProcessingStage.SENT)
                return BridgeResult(
                    handled=True, note="author is not a campaign member",
                    responses=[OutboundMessage(inbound.channel_id, _NOT_A_MEMBER)],
                )

            character = await CharacterService(session).get_active_character(member)

            # Is Session Zero active and is this the owner answering it?
            in_setup = (
                self.session_zero is not None
                and self.session_zero.is_active(campaign)
                and campaign.owner_user_id == (
                    await CampaignService(session).get_or_create_user(
                        inbound.author_discord_id, inbound.author_display_name
                    )
                ).id
            )

            # Is this member mid character-creation? Their plain messages belong to
            # that conversation, not to the classifier or the action pipeline.
            in_creation = False
            if self.creation_flow is not None:
                draft = await self.creation_flow.active_draft(session, member.id)
                in_creation = draft is not None

            # Is there a pending clarification owned by THIS member? If so, this
            # message (whatever it is) resolves it.
            pending = None
            if active_session is not None:
                scene = await SceneService(session).get_active_scene(active_session.id)
                if (
                    scene is not None
                    and scene.pending_action
                    and scene.pending_action.get("member_id") == member.id
                ):
                    pending = scene.pending_action

            ctx = ResolvedContext(
                inbound=inbound,
                campaign_id=campaign.id,
                member_id=member.id,
                session_id=active_session.id if active_session else None,
                character_id=character.id if character else None,
                processed_message_id=pm.id,
                pending_action=pending,
            )
            committed = detect_commitment(inbound)
            if committed is not None or pending is not None:
                await pm_service.set_category(pm, MessageCategory.COMMITTED_ACTION)
            campaign_snapshot = campaign  # primitives read post-txn are safe

        # Post-session one-tap feedback (armed by SessionClosingService).
        if committed is None and not in_setup and not in_creation:
            from app.services.sessions.closing_service import SessionClosingService

            if await SessionClosingService.try_record_feedback(
                self.db, campaign=campaign_snapshot, member_id=ctx.member_id,
                text=inbound.content,
            ):
                return BridgeResult(handled=True, responses=[OutboundMessage(
                    ctx.channel_id, "รับไว้แล้ว ขอบใจนะ 🙏", kind=MessageKind.TABLE_NOTICE,
                )])

        # --- routing (outside the resolution transaction) -------------------
        if in_setup:
            return await self.session_zero.handle_message(
                campaign_id=ctx.campaign_id, channel_id=ctx.channel_id,
                text=inbound.content,
            )
        if in_creation:
            return await self.creation_flow.handle_message(
                member_id=ctx.member_id, channel_id=ctx.channel_id,
                text=inbound.content,
            )
        if ctx.pending_action is not None:
            return await self._route_clarification_answer(ctx)
        if committed is not None:
            return await self._route_committed(ctx, committed)
        return await self._route_non_committed(ctx)

    # --- routing helpers -----------------------------------------------------
    async def _route_committed(
        self, ctx: ResolvedContext, action: CommittedAction
    ) -> BridgeResult:
        if ctx.session_id is None:
            msg = "ยังไม่ได้เริ่มเซสชัน เริ่มเซสชันก่อนถึงจะลงมือได้"
            return BridgeResult(
                handled=True, category=MessageCategory.COMMITTED_ACTION,
                responses=[OutboundMessage(ctx.channel_id, msg)], note="no active session",
            )
        if self.pipeline is None:
            return BridgeResult(
                handled=True, category=MessageCategory.COMMITTED_ACTION,
                note="committed pipeline not wired (Phase 7)",
            )
        # Serialize per session: one committed action at a time, in arrival order.
        async def work() -> BridgeResult:
            return await self.pipeline.handle(ctx, action)

        try:
            return await self.serializer.run(ctx.session_id, work)
        except Exception as exc:  # noqa: BLE001 - shape by pipeline stage, never leak raw
            return await self._recover_committed_failure(ctx, exc)

    async def _route_clarification_answer(self, ctx: ResolvedContext) -> BridgeResult:
        if self.pipeline is None or ctx.session_id is None:
            return BridgeResult(handled=True, note="pending clarification but no pipeline")

        async def work() -> BridgeResult:
            return await self.pipeline.resume_clarification(
                ctx, answer_text=ctx.inbound.content, pending=ctx.pending_action
            )

        try:
            return await self.serializer.run(ctx.session_id, work)
        except Exception as exc:  # noqa: BLE001
            return await self._recover_committed_failure(ctx, exc)

    # --- error recovery (§32 + experience overhaul) ---------------------------
    async def _recover_committed_failure(
        self, ctx: ResolvedContext, exc: Exception
    ) -> BridgeResult:
        """A committed action failed mid-pipeline. Consult the recorded stage and
        tell the player the truth about their action's fate — never a bare
        'internal error', and NEVER re-executing anything already committed."""
        log.exception("committed action failed (message %s)",
                      ctx.inbound.discord_message_id, exc_info=exc)

        pm = None
        if ctx.processed_message_id:
            async with self.db.session() as s:
                pm = await s.get(ProcessedMessage, ctx.processed_message_id)
        stage = pm.stage if pm else ProcessingStage.RECEIVED.value

        committed = stage in (
            ProcessingStage.COMMITTED.value,
            ProcessingStage.NARRATED.value,
            ProcessingStage.SENT.value,
        )
        if committed:
            # STATE COMMITTED, NARRATION/DELIVERY FAILED: restate the committed
            # fact from the record; the result stands as-is.
            result = (pm.result or {}) if pm else {}
            roll_line = result.get("roll_line") or result.get("outcome") or "ผลถูกบันทึกแล้ว"
            content = "การกระทำของเจ้าเกิดขึ้นแล้วและผลถูกบันทึกไว้เรียบร้อย\nDM เล่าต่อไม่จบ แต่ผลยืนตามนี้"
            data = {"roll_line": roll_line, "footer": "ผลนี้ถือเป็นที่สิ้นสุด — ไม่ต้องพิมพ์ซ้ำ"}
            note = "recovered: committed-but-unnarrated"
        else:
            # PROCESSING FAILED BEFORE STATE COMMIT: nothing happened; mark FAILED
            # so a retype starts clean, and say so plainly.
            if pm is not None:
                async with self.db.unit_of_work() as s:
                    row = await s.get(ProcessedMessage, pm.id)
                    if row is not None:
                        row.stage = ProcessingStage.FAILED.value
            content = (
                "DM สะดุดตอนกำลังตัดสินการกระทำนี้ — ยังไม่มีอะไรเกิดขึ้นกับตัวละครของเจ้า\n"
                "พิมพ์การกระทำเดิมอีกครั้งได้เลย"
            )
            data = {"footer": "ยังไม่ถูกบันทึก — ปลอดภัยที่จะลองใหม่"}
            note = "recovered: failed-before-commit"

        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION,
            state_mutated=committed, note=note,
            responses=[OutboundMessage(
                ctx.channel_id, content, kind=MessageKind.TECHNICAL_ERROR,
                title="โต๊ะสะดุดเล็กน้อย", data=data,
            )],
        )

    async def _route_non_committed(self, ctx: ResolvedContext) -> BridgeResult:
        if self.router is None:
            # Phase 4 skeleton: no classifier yet; no state is ever mutated here.
            return BridgeResult(handled=True, note="non-committed router not wired (Phase 5)")
        return await self.router.handle(ctx)


_NOT_A_MEMBER = "เจ้ายังไม่ได้เป็นสมาชิกแคมเปญนี้"
