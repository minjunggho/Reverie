"""TravelService — natural movement between canonical locations (the walk-outside fix).

`! ผมเดินออกไปข้างนอก` → resolve the exit against the world graph → validate access →
advance the world clock (ticks threats/scheduled events) → move the party → transition
the scene → FRAME THE DESTINATION FROM CANON. The narrator never invents the
destination; the engine resolves it first, then describes only authored facts.

When no authored exit matches, WorldExpansionService may commit a canon-consistent
ordinary place BEFORE narration (and it persists).
"""
from __future__ import annotations

from app.ai.llm.base import LLMProvider
from app.ai.narration_guard import screen_decision_prompt, screen_narration
from app.core.errors import LLMError
from app.core.ids import parse_entity_ref
from app.discord_bridge.dto import BridgeResult, OutboundMessage
from app.memory.context_builders import build_scene_frame_context
from app.memory.scene_context import SceneContextBuilder
from app.models.character import Character
from app.models.enums import ActivePlayState, MessageCategory, ProcessingStage
from app.models.processed_message import ProcessedMessage
from app.models.session import Session
from app.presentation import MessageKind
from app.schemas.llm_io import Narration
from app.services.scenes import SceneService
from app.world.expansion_service import WorldExpansionService
from app.world.graph_service import WorldGraphService
from app.world.position_service import PositionService
from app.world.world_clock import WorldClockService


class TravelService:
    def __init__(self, db, provider: LLMProvider) -> None:
        self.db = db
        self.provider = provider
        self.expansion = WorldExpansionService(db, provider)

    async def travel(self, ctx, *, reference: str) -> BridgeResult:
        async with self.db.session() as read:
            scene = await SceneService(read).get_active_scene(ctx.session_id) if ctx.session_id else None
            actor = await read.get(Character, ctx.character_id) if ctx.character_id else None
            current_id = (actor.location_id if actor else None) or (scene.location_id if scene else None)
            actor_name = actor.name if actor else "ตัวละครของเจ้า"
            current_name = ""
            match = None
            if current_id:
                from app.models.location import Location

                cur_loc = await read.get(Location, current_id)
                current_name = cur_loc.name if cur_loc else ""
                match = await WorldGraphService(read).resolve_exit(
                    from_location_id=current_id, reference=reference)

        if current_id is None:
            return self._note(ctx, "ยังไม่รู้ว่าตัวละครอยู่ตรงไหนในโลก — เริ่มเซสชันก่อน")

        # Resolve destination: authored exit, else canon-consistent expansion.
        travel_minutes = 0
        if match is not None:
            if match.connection.access_state != "open":
                return self._note(
                    ctx, f"ทาง{match.connection.label or 'นั้น'}ตอนนี้{_access_th(match.connection.access_state)}")
            dest_id = match.connection.to_location_id
            travel_minutes = match.connection.travel_minutes
        else:
            dest = await self.expansion.find_or_expand(
                campaign_id=ctx.campaign_id, from_location_id=current_id, request=reference)
            if dest is None:
                # A focused, CHARACTER-facing clarification — not "what's out there?".
                return self._note(
                    ctx, f"{actor_name}จะไปทางไหน? บอกทิศทางหรือชื่อสถานที่หน่อย")
            dest_id = dest.id
            async with self.db.session() as read:
                for e in await WorldGraphService(read).exits(current_id):
                    if e.to_location_id == dest_id:
                        travel_minutes = e.travel_minutes
                        break

        # Advance the world (threats/events tick), move the party together, transition.
        perceivable: list[str] = []
        async with self.db.unit_of_work() as s:
            if travel_minutes > 0:
                clock = await WorldClockService(s).advance_time(
                    campaign_id=ctx.campaign_id, minutes=travel_minutes,
                    session_id=ctx.session_id, actor_entity=f"character:{ctx.character_id}")
                perceivable = list(clock.perceivable_notes)
            scene_row = await SceneService(s).get_active_scene(ctx.session_id) if ctx.session_id else None
            movers: list[str] = []
            if scene_row is not None:
                for ref in list(scene_row.participants or []):
                    kind, cid = parse_entity_ref(ref)
                    if kind == "character" and cid:
                        movers.append(cid)
                if ctx.character_id and ctx.character_id not in movers:
                    movers.append(ctx.character_id)
                for cid in movers:
                    await PositionService(s).move(
                        character_id=cid, to_location_id=dest_id, campaign_id=ctx.campaign_id,
                        session_id=ctx.session_id, from_location_id=current_id)
                scene_row.location_id = dest_id
                scene_row.pending_action = None
                scene_row.pending_action_id = None
                scene_row.version += 1
            session_row = await s.get(Session, ctx.session_id) if ctx.session_id else None
            if session_row is not None:
                session_row.active_play_state = ActivePlayState.TABLE_OPEN.value
                session_row.version += 1
            pm = await s.get(ProcessedMessage, ctx.processed_message_id) if ctx.processed_message_id else None
            if pm is not None:
                pm.stage = ProcessingStage.SENT.value
                pm.category = MessageCategory.COMMITTED_ACTION.value

        # Frame the DESTINATION from canonical context.
        async with self.db.session() as read:
            scene2 = await SceneService(read).get_active_scene(ctx.session_id) if ctx.session_id else None
            sctx = await SceneContextBuilder(read).build(
                campaign_id=ctx.campaign_id, scene=scene2, actor_character_id=ctx.character_id)
            narration = await self._frame(read, sctx, arrival_from=current_name)

        text, _ = screen_narration(narration.text, actor_name)
        prompt = screen_decision_prompt(narration.decision_prompt or "จะทำอะไรต่อ?", actor_name)
        data = {"decision_prompt": prompt}
        if sctx.exits:
            data["fields"] = [{"name": "ทางออก", "value": "\n".join(sctx.exits), "inline": False}]
        responses = [OutboundMessage(ctx.channel_id, text, kind=MessageKind.SCENE_FRAME,
                                     title=sctx.location_name, data=data)]
        if perceivable:
            responses.append(OutboundMessage(
                ctx.channel_id, "\n".join(perceivable), kind=MessageKind.SCENE_TRANSITION,
                title="ระหว่างทาง"))
        return BridgeResult(handled=True, category=MessageCategory.COMMITTED_ACTION,
                            state_mutated=True, responses=responses,
                            note=f"travel -> {sctx.location_name}")

    async def _frame(self, read, sctx, *, arrival_from: str) -> Narration:
        messages = build_scene_frame_context(sctx, arrival_from=arrival_from)
        try:
            return await self.provider.frame_scene(messages)
        except LLMError:
            # Deterministic canonical fallback: the authored description IS the scene.
            body = sctx.location_obvious or f"เจ้ามาถึง{sctx.location_name}"
            if sctx.current_activity:
                body += f"\n{sctx.current_activity}"
            return Narration(text=body, decision_prompt="จะทำอะไรต่อ?")

    def _note(self, ctx, text: str) -> BridgeResult:
        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION, state_mutated=False,
            responses=[OutboundMessage(ctx.channel_id, text, kind=MessageKind.TABLE_NOTICE)],
            note="travel unresolved")


def _access_th(state: str) -> str:
    return {"locked": "ถูกล็อกอยู่", "blocked": "ถูกปิดกั้น", "hidden": "ยังไม่เห็น"}.get(state, "ผ่านไม่ได้")
