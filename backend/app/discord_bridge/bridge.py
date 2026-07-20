"""DiscordBridge — resolution, idempotency, serialization, and routing.

Pipeline (see §10):
  receive -> validate -> idempotency -> resolve campaign(by channel) -> resolve
  active session -> resolve member(by discord user) -> resolve active character ->
  detect `!` -> route (committed -> serialized pipeline; else -> classifier router).

The bridge itself contains NO game logic. Committed actions go through the
per-session serializer so they are handled one at a time in arrival order.
"""
from __future__ import annotations

import re
from typing import Protocol

from sqlalchemy import select

from app.core.logging import get_logger
from app.discord_bridge.dto import BridgeResult, InboundMessage, OutboundMessage
from app.models.decision_window import ActionSubmission, DecisionWindow
from app.models.enums import MessageCategory, ProcessingStage, WindowMode, WindowPhase
from app.models.processed_message import ProcessedMessage
from app.orchestration.commitment import CommittedAction, detect_commitment
from app.orchestration.context import ResolvedContext
from app.orchestration.serializer import SessionSerializer
from app.presentation import MessageKind
from app.presentation.screens import cinematic_scene_screen, decision_window_screen
from app.rounds import DecisionWindowService, WindowPolicies

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
        round_resolver=None,
        creation_flow=None,  # CreationFlowService | None — guided character creation
        session_zero=None,   # SessionZeroService | None — table profile flow
    ) -> None:
        self.db = db
        self.router = router
        self.pipeline = pipeline
        self.serializer = serializer or SessionSerializer()
        self.round_resolver = round_resolver
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
                draft = await self.creation_flow.active_draft(
                    session, campaign_id=campaign.id, member_id=member.id
                )
                in_creation = draft is not None

            # Is there a pending clarification owned by THIS member? If so, this
            # message (whatever it is) resolves it.
            pending = None
            active_window_id = None
            if active_session is not None:
                scene = await SceneService(session).get_active_scene(active_session.id)
                if (
                    scene is not None
                    and scene.pending_action
                    and scene.pending_action.get("member_id") == member.id
                ):
                    pending = scene.pending_action
                if scene is not None:
                    active_window = (await session.execute(
                        select(DecisionWindow).where(
                            DecisionWindow.scene_id == scene.id,
                            DecisionWindow.phase == WindowPhase.AWAITING_ACTIONS.value,
                        ).order_by(DecisionWindow.round_id.desc())
                    )).scalars().first()
                    active_window_id = active_window.id if active_window is not None else None

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
            window_control = _parse_window_control(inbound.content)
            if committed is not None or pending is not None or window_control is not None:
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
                campaign_id=ctx.campaign_id, member_id=ctx.member_id,
                channel_id=ctx.channel_id,
                text=inbound.content,
            )
        if ctx.pending_action is not None:
            return await self._route_clarification_answer(ctx)
        if window_control is not None:
            return await self._route_window_control(ctx, window_control)
        if committed is not None and active_window_id is not None:
            return await self._route_window_submission(ctx, committed, active_window_id)
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

    # --- shared DecisionWindow transport ------------------------------------
    async def _route_window_submission(
        self, ctx: ResolvedContext, action: CommittedAction, window_id: str,
    ) -> BridgeResult:
        """Collect or edit one player's intention; never resolve on first submit in a
        multiplayer window.  Readiness is re-checked from persisted rows."""
        if ctx.character_id is None:
            return self._window_notice(ctx, "ต้องมีตัวละครที่กำลังใช้งานก่อนส่งการกระทำ")

        async def work() -> BridgeResult:
            async with self.db.unit_of_work() as s:
                svc = DecisionWindowService(s)
                window = await svc.get(window_id)
                if ctx.character_id not in (window.required_actor_ids or []):
                    return self._window_notice(ctx, "ตัวละครของคุณไม่ได้อยู่ในรอบนี้")
                fields = (
                    {"dialogue": action.action_text, "primary_action": ""}
                    if action.is_speech else None
                )
                sub = await svc.submit(
                    window_id=window.id,
                    actor_id=ctx.character_id,
                    raw_text=action.action_text,
                    fields=fields,
                    idempotency_key=ctx.inbound.discord_message_id,
                )
                all_ready = await svc.all_required_ready(window)
                panel = await svc.panel(window, viewer_id=ctx.character_id)
            if all_ready:
                return await self._resolve_window(ctx, window_id, solo_action=action)
            return await self._window_panel_result(
                ctx, panel, notice=(
                    "บันทึกการกระทำแล้ว · พร้อมอัตโนมัติ"
                    if sub.is_ready else
                    "บันทึกการกระทำแล้ว · แก้ได้จนกว่าจะกดพร้อม"
                ),
            )

        try:
            return await self.serializer.run(ctx.session_id or window_id, work)
        except Exception as exc:  # noqa: BLE001
            log.exception("shared action submission failed", exc_info=exc)
            return self._window_notice(ctx, "บันทึกแผนรอบนี้ไม่สำเร็จ กรุณาลองอีกครั้ง")

    async def _route_window_control(
        self, ctx: ResolvedContext, control: tuple[str, str],
    ) -> BridgeResult:
        action, window_id = control
        is_host_action = action in _HOST_CONTROLS
        # Player controls need an active character; host controls only need the host.
        if ctx.character_id is None and not is_host_action:
            return self._window_notice(ctx, "ต้องมีตัวละครที่กำลังใช้งานก่อน")

        from app.core.errors import RulesViolation

        async def work() -> BridgeResult:
            do_resolve = False
            panel: dict | None = None
            notice = ""
            async with self.db.unit_of_work() as s:
                svc = DecisionWindowService(s)
                window = await svc.get(window_id)
                if window.campaign_id != ctx.campaign_id or window.session_id != ctx.session_id:
                    return self._window_notice(ctx, "ปุ่มนี้ไม่ใช่รอบปัจจุบันของโต๊ะ")

                # --- host controls: owner-only, allowed regardless of phase ------
                if is_host_action:
                    if not await self._is_host(s, ctx):
                        return self._window_notice(
                            ctx, "เฉพาะผู้ดูแลโต๊ะเท่านั้นที่ใช้ปุ่มนี้ได้")
                    if action == "force":
                        if window.resolved:
                            return self._window_notice(ctx, "รอบนี้จบไปแล้ว")
                        await svc.force_resolve(window)      # freeze even if not all ready
                        do_resolve = True
                    else:  # reopen: pull planning back so players can edit/ready again
                        try:
                            await svc.reopen(window)
                        except RulesViolation:
                            return self._window_notice(
                                ctx, "รอบนี้ resolve ไปแล้ว เปิดวางแผนใหม่ไม่ได้ — "
                                     "รอบถัดไปเปิดให้อัตโนมัติ")
                        panel = await svc.panel(window, viewer_id=ctx.character_id)
                        notice = "ผู้ดูแลเปิดวางแผนรอบนี้ใหม่ · แก้และกดพร้อมได้อีกครั้ง"
                else:
                    # --- player controls: require open phase + membership -------
                    if window.phase != WindowPhase.AWAITING_ACTIONS.value:
                        return self._window_notice(ctx, "รอบนี้ปิดรับการแก้ไขแล้ว")
                    if ctx.character_id not in (window.required_actor_ids or []):
                        return self._window_notice(ctx, "ตัวละครของคุณไม่ได้อยู่ในรอบนี้")
                    if action == "ready":
                        sub = await svc._submission(window.id, ctx.character_id)
                        if sub is None:
                            return self._window_notice(
                                ctx, "ส่งการกระทำด้วย `! ...` ก่อน แล้วจึงกดพร้อม")
                        await svc.mark_ready(
                            window_id=window.id, actor_id=ctx.character_id,
                            revision=sub.revision)
                        notice = "พร้อมแล้ว · ระบบจะรอผู้เล่นที่เหลือ"
                    elif action == "unready":
                        await svc.unmark_ready(window_id=window.id, actor_id=ctx.character_id)
                        notice = "ยกเลิก Ready แล้ว · แก้การกระทำได้"
                    else:
                        await svc.pass_turn(window_id=window.id, actor_id=ctx.character_id)
                        notice = "ผ่านรอบนี้แล้ว"
                    do_resolve = await svc.all_required_ready(window)
                    if not do_resolve:
                        panel = await svc.panel(window, viewer_id=ctx.character_id)
            if do_resolve:
                return await self._resolve_window(ctx, window_id)
            return await self._window_panel_result(ctx, panel, notice=notice)

        try:
            return await self.serializer.run(ctx.session_id or window_id, work)
        except Exception as exc:  # noqa: BLE001
            log.exception("shared planning control failed", exc_info=exc)
            return self._window_notice(ctx, "อัปเดตสถานะรอบนี้ไม่สำเร็จ กรุณาลองอีกครั้ง")

    @staticmethod
    async def _is_host(session, ctx: ResolvedContext) -> bool:
        """The campaign owner is the host. Checked from the member row, never trusted
        from the client."""
        from app.models.campaign import CampaignMember
        from app.models.enums import MemberRole

        member = await session.get(CampaignMember, ctx.member_id) if ctx.member_id else None
        return member is not None and member.role == MemberRole.OWNER.value

    async def _resolve_window(
        self, ctx: ResolvedContext, window_id: str, *,
        solo_action: CommittedAction | None = None,
    ) -> BridgeResult:
        # Single-player uses the SAME window/ready UX, but one action resolves through
        # the FULL committed pipeline — so a solo player keeps every domain flow (spell
        # slots, saving throws, travel, item transfer, dice ritual) at full depth,
        # never the shared resolver's coordination-only intent recording.
        async with self.db.session() as s:
            window = await s.get(DecisionWindow, window_id)
            required = list(window.required_actor_ids or []) if window else []
        if len(required) == 1 and self.pipeline is not None:
            return await self._resolve_solo_via_pipeline(
                ctx, window_id, required[0], solo_action)

        if self.round_resolver is None:
            return self._window_notice(
                ctx, "ทุกคนพร้อมแล้ว แต่ตัวแก้รอบร่วมยังไม่ได้เชื่อมต่อ")
        pkg = await self.round_resolver.resolve(window_id=window_id)

        # Open the next planning window only after this package is fully persisted.
        next_window_id: str | None = None
        required: list[str] = []
        async with self.db.unit_of_work() as s:
            old = await s.get(DecisionWindow, window_id)
            if old is not None and old.scene_id is not None:
                required = list(old.required_actor_ids or [])
                policies = WindowPolicies.from_config({"planning": old.config})
                next_window = await DecisionWindowService(s).open_window(
                    campaign_id=old.campaign_id,
                    session_id=old.session_id,
                    scene_id=old.scene_id,
                    round_id=old.round_id + 1,
                    mode=WindowMode(old.mode),
                    required_actor_ids=required,
                    policies=policies,
                )
                next_window_id = next_window.id

        packet = dict(pkg.scene_packet or {})
        names = {
            str(c.get("id")): str(c.get("name"))
            for c in packet.get("player_characters", [])
            if c.get("id") and c.get("name")
        }
        prose = (pkg.narration or "").strip() or self._round_fallback(pkg, names)
        prompt = (pkg.decision_prompt or "").strip() or "พวกคุณจะทำอย่างไรต่อ?"
        metadata = self._packet_metadata(packet)
        planning_status = [
            f"○ **{names.get(actor_id, actor_id)}** — รอการกระทำ"
            for actor_id in required
        ]
        screen = cinematic_scene_screen(
            metadata=metadata,
            narration=prose,
            decision_prompt=prompt,
            planning_window_id=next_window_id,
            planning_status=planning_status,
        )

        mechanic_lines: list[str] = []
        for roll in pkg.roll_results:
            verdict = "โดน" if roll.get("hit") else "พลาด"
            mechanic_lines.append(
                f"{names.get(roll.get('actor_id'), roll.get('actor_id'))}: "
                f"{roll.get('attack_total')} vs AC {roll.get('target_ac')} — {verdict}"
            )
        for damage in pkg.damage:
            mechanic_lines.append(
                f"ความเสียหาย {damage.get('amount')} → {damage.get('target')}"
            )
        responses: list[OutboundMessage] = []
        if mechanic_lines:
            responses.append(OutboundMessage(
                ctx.channel_id,
                "\n".join(mechanic_lines),
                kind=MessageKind.CHECK_RESOLUTION,
                title=f"ผลรอบ {pkg.round_id}",
                data={"round_id": pkg.round_id, "rolls_verified": True},
            ))
        responses.append(OutboundMessage(
            ctx.channel_id,
            screen.to_text(),
            kind=MessageKind.SCENE_FRAME,
            data={
                "decision_prompt": prompt,
                "decision_window_id": next_window_id,
                "round_id": pkg.round_id,
                "connected_scene": True,
            },
            screen=screen,
        ))
        return BridgeResult(
            handled=True,
            category=MessageCategory.COMMITTED_ACTION,
            state_mutated=True,
            responses=responses,
            note=f"shared round {pkg.round_id} resolved",
        )

    async def _resolve_solo_via_pipeline(
        self, ctx: ResolvedContext, window_id: str, actor_id: str,
        solo_action: CommittedAction | None,
    ) -> BridgeResult:
        """Resolve a one-actor window at full pipeline depth, then consume this planning
        slot and open the next window. We are already inside the per-session lock, so the
        pipeline runs directly (no re-entrant serialize)."""
        action = solo_action
        if action is None:
            async with self.db.session() as s:
                sub = (await s.execute(select(ActionSubmission).where(
                    ActionSubmission.window_id == window_id,
                    ActionSubmission.actor_id == actor_id))).scalars().first()
            text = sub.raw_player_text if sub else ""
            if not text:
                return self._window_notice(ctx, "ยังไม่มีการกระทำให้ resolve")
            action = CommittedAction(action_text=text, is_speech=bool(sub and sub.dialogue
                                                                      and not sub.primary_action))
        result = await self.pipeline.handle(ctx, action)

        # Consume this planning slot and open the next window for the scene. Done even if
        # the pipeline paused for a dice ritual — the pending roll resolves via the
        # scene's pending-action path (which routes ahead of any window), and the newly
        # opened window governs the player's NEXT action.
        async with self.db.unit_of_work() as s:
            window = await s.get(DecisionWindow, window_id)
            if window is not None and not window.resolved:
                window.resolved = True
                window.phase = WindowPhase.ROUND_COMPLETE.value
                window.version += 1
                if window.scene_id is not None:
                    await DecisionWindowService(s).open_window(
                        campaign_id=window.campaign_id,
                        session_id=window.session_id,
                        scene_id=window.scene_id,
                        round_id=window.round_id + 1,
                        mode=WindowMode(window.mode),
                        required_actor_ids=list(window.required_actor_ids or []),
                        policies=WindowPolicies.from_config({"planning": window.config}),
                    )
        return result

    async def _window_panel_result(
        self, ctx: ResolvedContext, panel: dict, *, notice: str,
    ) -> BridgeResult:
        actor_ids = {
            *panel.get("waiting_on", []),
            *panel.get("not_submitted", []),
            *(c.get("actor_id") for c in panel.get("cards", [])),
        }
        from app.models.character import Character

        async with self.db.session() as s:
            chars = list((await s.execute(select(Character).where(
                Character.id.in_([x for x in actor_ids if x])
            ))).scalars()) if actor_ids else []
        names = {c.id: c.name for c in chars}
        # Required actors who are already Ready are absent from waiting_on; recover
        # them from the window row so the panel never makes a player disappear.
        async with self.db.session() as s:
            window = await s.get(DecisionWindow, panel["window_id"])
            if window is not None:
                for actor_id in window.required_actor_ids or []:
                    if actor_id not in names:
                        char = await s.get(Character, actor_id)
                        names[actor_id] = char.name if char else actor_id
        screen = decision_window_screen(
            window_id=panel["window_id"],
            round_id=panel["round_id"],
            cards=panel.get("cards", []),
            actor_names=names,
            viewer_actor_id=ctx.character_id,
            notice=notice,
        )
        return BridgeResult(
            handled=True,
            category=MessageCategory.COMMITTED_ACTION,
            state_mutated=True,
            responses=[OutboundMessage(
                ctx.channel_id,
                screen.to_text(),
                kind=MessageKind.TABLE_NOTICE,
                data={"decision_window_id": panel["window_id"], "panel": panel},
                screen=screen,
            )],
            note="decision window updated",
        )

    @staticmethod
    def _packet_metadata(packet: dict) -> str:
        condition = packet.get("weather") or "สภาพอากาศไม่ระบุ"
        return (
            f"| {packet.get('day_segment', 'เวลาไม่ระบุ')} | "
            f"{packet.get('clock', '--:--')} น. | วันที่ {packet.get('day', '?')} | "
            f"{packet.get('location', '-')} | {condition} |"
        )

    @staticmethod
    def _round_fallback(pkg, names: dict[str, str]) -> str:
        lines: list[str] = []
        intentions = {x.get("actor_id"): x for x in pkg.intentions}
        for resolved in pkg.resolved_actions:
            actor_id = resolved.get("actor_id")
            raw = intentions.get(actor_id, {}).get("raw_player_text") or ""
            status = resolved.get("status")
            if status == "invalidated":
                lines.append(
                    f"{names.get(actor_id, actor_id)} ต้องชะงัก—"
                    f"{resolved.get('note') or 'สถานการณ์เปลี่ยนไปก่อน'}"
                )
            elif status == "fallback":
                lines.append(
                    f"{names.get(actor_id, actor_id)} เปลี่ยนไปใช้แผนสำรองตามที่ประกาศไว้"
                )
            elif raw:
                lines.append(f"{names.get(actor_id, actor_id)} เริ่มลงมือ: {raw}")
        return "\n\n".join(lines) or "ทุกคนขยับตามแผนที่ตกลงกันไว้ ขณะที่ฉากยังเดินหน้าต่อ"

    @staticmethod
    def _window_notice(ctx: ResolvedContext, text: str) -> BridgeResult:
        return BridgeResult(
            handled=True,
            category=MessageCategory.COMMITTED_ACTION,
            responses=[OutboundMessage(
                ctx.channel_id, text, kind=MessageKind.TABLE_NOTICE)],
        )

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

_WINDOW_CONTROL_RE = re.compile(
    r"^~rv-(ready|unready|pass|force|reopen):([A-Za-z0-9_-]{8,64})$"
)
# Controls only the campaign host (owner) may invoke.
_HOST_CONTROLS = frozenset({"force", "reopen"})


def _parse_window_control(text: str) -> tuple[str, str] | None:
    """Parse only opaque values emitted by Reverie's own planning controls."""
    match = _WINDOW_CONTROL_RE.fullmatch((text or "").strip())
    return (match.group(1), match.group(2)) if match else None
