"""TravelService — natural movement between canonical locations (the walk-outside fix).

`! ผมเดินออกไปข้างนอก` → resolve the exit against the world graph → validate access →
advance the world clock (ticks threats/scheduled events) → move the party → transition
the scene → FRAME THE DESTINATION FROM CANON. The narrator never invents the
destination; the engine resolves it first, then describes only authored facts.

When no authored exit matches, WorldExpansionService may commit a canon-consistent
ordinary place BEFORE narration (and it persists).
"""
from __future__ import annotations

from sqlalchemy import select

from app.ai.llm.base import LLMProvider
from app.ai.narration_guard import screen_decision_prompt, screen_narration
from app.core.errors import LLMError
from app.discord_bridge.dto import BridgeResult, OutboundMessage
from app.memory.context_builders import build_scene_frame_context
from app.memory.scene_context import SceneContextBuilder
from app.models.character import Character
from app.models.enums import ActivePlayState, MessageCategory, ProcessingStage, SceneMode
from app.models.npc import NPC
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

    async def travel(self, ctx, *, reference: str, allow_expansion: bool = True) -> BridgeResult:
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
            if not allow_expansion:
                # CANONICAL_TRAVEL/RETURN_OR_EXIT with no matching edge: a focused
                # navigation clarification, never a fabricated Location (Fix 6).
                return self._note(
                    ctx, f"{actor_name}ไม่รู้ทางไปตรงนั้น — มีทางไหนที่รู้จักบ้าง?")
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

        # Advance the world (threats/events tick), move the party, then a REAL scene
        # transition: close the origin Scene and open a fresh one at the destination
        # (Fix 5). A Scene is not a Location — nothing location-specific (present
        # cast, objects, threats, clues, purpose) carries over; it is rebuilt from
        # canon at the destination, never inherited from the scene being left.
        perceivable: list[str] = []
        game_time = 0
        async with self.db.unit_of_work() as s:
            if travel_minutes > 0:
                clock = await WorldClockService(s).advance_time(
                    campaign_id=ctx.campaign_id, minutes=travel_minutes,
                    session_id=ctx.session_id, actor_entity=f"character:{ctx.character_id}")
                perceivable = list(clock.perceivable_notes)
            from app.models.campaign import Campaign

            campaign_row = await s.get(Campaign, ctx.campaign_id)
            game_time = campaign_row.current_game_time if campaign_row else 0
            # The active scene IS the table's focus: its move drags the party anchor
            # (the continuity point the next session opens from).
            if campaign_row is not None:
                campaign_row.current_party_anchor_id = dest_id
            scene_row = await SceneService(s).get_active_scene(ctx.session_id) if ctx.session_id else None
            if scene_row is not None:
                # MOVEMENT CONSENT (§18): only the acting character moves by default.
                # Co-location is NOT consent. Another character travels along ONLY
                # when it has an explicit, persistent follow state pointing at the
                # actor AND is co-located right now. Everyone else stays put — a split
                # party remains split across scenes and sessions.
                movers: list[str] = [ctx.character_id] if ctx.character_id else []
                if ctx.character_id:
                    followers = await PositionService(s).consenting_followers(
                        campaign_id=ctx.campaign_id, leader_id=ctx.character_id,
                        at_location_id=current_id)
                    movers.extend(followers)
                    # The actor moving on their own initiative is now leading, not
                    # following — break any stale follow state they held.
                    await PositionService(s).stop_follow(follower_id=ctx.character_id)
                for cid in movers:
                    await PositionService(s).move(
                        character_id=cid, to_location_id=dest_id, campaign_id=ctx.campaign_id,
                        session_id=ctx.session_id, from_location_id=current_id)

                dest_npc_refs = [f"npc:{n.id}" for n in (await s.execute(
                    select(NPC).where(NPC.campaign_id == ctx.campaign_id,
                                      NPC.current_location_id == dest_id))).scalars()]
                await SceneService(s).close_scene(scene_row)
                await SceneService(s).create_scene(
                    session_id=scene_row.session_id, location_id=dest_id,
                    mode=SceneMode(scene_row.mode) if scene_row.mode else SceneMode.EXPLORATION,
                    participants=[f"character:{cid}" for cid in movers],
                    visible_entity_ids=dest_npc_refs,
                    scene_start_game_time=game_time,
                )
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
        # Authoritative time in the frame footer — players always know when time moved.
        from app.core.clock import format_game_time_th

        footer = format_game_time_th(game_time)
        if travel_minutes > 0:
            footer += f" · เดินทาง {travel_minutes} นาที"
        data["footer"] = footer
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
