"""TravelService — natural movement between canonical locations (the walk-outside fix).

`! ผมเดินออกไปข้างนอก` → resolve the exit against the world graph → validate access →
advance the world clock (ticks threats/scheduled events) → move the party → transition
the scene → FRAME THE DESTINATION FROM CANON. The narrator never invents the
destination; the engine resolves it first, then describes only authored facts.

When no authored exit matches, WorldExpansionService may commit a canon-consistent
ordinary place BEFORE narration (and it persists).
"""
from __future__ import annotations

from dataclasses import dataclass, field

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
from app.models.world_graph import LocationConnection
from app.presentation import MessageKind
from app.schemas.llm_io import Narration
from app.services.scenes import SceneService
from app.world.expansion_service import WorldExpansionService
from app.world.graph_service import WorldGraphService
from app.world.position_service import PositionService
from app.world.route_service import RoutePlan
from app.world.world_clock import WorldClockService


@dataclass
class _WalkResult:
    """Outcome of a segment-by-segment route walk."""

    reached_id: str
    elapsed_minutes: int
    perceivable: list[str] = field(default_factory=list)
    waypoints: list[str] = field(default_factory=list)
    blocked_note: str = ""


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

        # Resolve to an ordered ROUTE (list of connection ids to walk), in priority:
        #   1. a direct authored exit (fast path, one hop);
        #   2. a MULTI-HOP route to a named/aliased/NPC-located destination — the
        #      outside rule falls out of routing (tavern → street → … → shop), and a
        #      missing exterior link is inferred so the world stays explorable;
        #   3. bounded expansion of an ordinary place (one hop);
        #   4. a focused, character-facing clarification. Never a fabricated
        #      destination chosen "because it was the only edge".
        waypoints: list[str] = []
        # A STRONG adjacent match (a named/labelled/directional exit) is the fast path.
        # The weak single-exit "just leave" fallback (conf 0.7) is held back: a NAMED
        # destination elsewhere outranks "go through the only door" (§5).
        strong = match if (match is not None and match.confidence >= 0.8) else None
        if strong is not None:
            if strong.connection.access_state != "open":
                return self._note(
                    ctx, f"ทาง{strong.connection.label or 'นั้น'}ตอนนี้{_access_th(strong.connection.access_state)}")
            hop_ids = [strong.connection.id]
        else:
            routed = await self._resolve_multi_hop(ctx, current_id=current_id, reference=reference)
            if isinstance(routed, RoutePlan):
                hop_ids = [h.id for h in routed.hops]
                waypoints = list(routed.waypoint_names)
            elif isinstance(routed, str):
                # Ambiguous, or a real place the party simply can't reach right now.
                return self._note(ctx, routed)
            elif match is not None:
                # No named route, but there IS one obvious way out — honor it.
                if match.connection.access_state != "open":
                    return self._note(
                        ctx, f"ทาง{match.connection.label or 'นั้น'}ตอนนี้{_access_th(match.connection.access_state)}")
                hop_ids = [match.connection.id]
            elif not allow_expansion:
                # CANONICAL_TRAVEL/RETURN_OR_EXIT with no matching edge/route: a
                # focused navigation clarification, never a fabricated Location.
                return self._note(
                    ctx, f"{actor_name}ไม่รู้ทางไปตรงนั้น — มีทางไหนที่รู้จักบ้าง?")
            else:
                dest = await self.expansion.find_or_expand(
                    campaign_id=ctx.campaign_id, from_location_id=current_id, request=reference)
                if dest is None:
                    # A focused, CHARACTER-facing clarification — not "what's out there?".
                    return self._note(
                        ctx, f"{actor_name}จะไปทางไหน? บอกทิศทางหรือชื่อสถานที่หน่อย")
                async with self.db.session() as read:
                    hop_ids = [e.id for e in await WorldGraphService(read).exits(current_id)
                               if e.to_location_id == dest.id][:1]

        # Walk the route SEGMENT BY SEGMENT (§7): each hop is re-validated as still
        # open at execution time, its own travel-time is advanced (ticking threats /
        # scheduled events at the right moment), and every mover's position is updated
        # one hop at a time. If a hop is blocked midway, the party STOPS at the last
        # valid location — elapsed time and completed segments are preserved, and the
        # actor is never teleported to the final destination. Then a REAL scene
        # transition: close the origin Scene and open a fresh one at the reached
        # location, rebuilt from canon (never inherited from the scene being left).
        perceivable: list[str] = []
        game_time = 0
        travel_minutes = 0
        blocked_note = ""
        async with self.db.unit_of_work() as s:
            # MOVEMENT CONSENT (§18): only the acting character moves by default.
            # Co-location is NOT consent. Another character travels along ONLY when it
            # has an explicit, persistent follow state pointing at the actor AND is
            # co-located right now. Everyone else stays put — a split party stays split.
            movers: list[str] = [ctx.character_id] if ctx.character_id else []
            if ctx.character_id:
                movers.extend(await PositionService(s).consenting_followers(
                    campaign_id=ctx.campaign_id, leader_id=ctx.character_id,
                    at_location_id=current_id))
                # The actor moving on their own initiative is now leading, not
                # following — break any stale follow state they held.
                await PositionService(s).stop_follow(follower_id=ctx.character_id)

            walk = await self._execute_route(
                s, ctx=ctx, hop_ids=hop_ids, movers=movers, origin_id=current_id)
            reached_id = walk.reached_id
            travel_minutes = walk.elapsed_minutes
            perceivable = walk.perceivable
            blocked_note = walk.blocked_note
            waypoints = walk.waypoints            # actually traversed, not merely planned

            from app.models.campaign import Campaign

            campaign_row = await s.get(Campaign, ctx.campaign_id)
            game_time = campaign_row.current_game_time if campaign_row else 0
            if reached_id == current_id:
                # The very first hop was blocked — nobody moved; no scene transition.
                return self._note(
                    ctx, blocked_note or f"{actor_name}ไปทางนั้นไม่ได้ตอนนี้")
            # The active scene IS the table's focus: its move drags the party anchor
            # (the continuity point the next session opens from).
            if campaign_row is not None:
                campaign_row.current_party_anchor_id = reached_id
            scene_row = await SceneService(s).get_active_scene(ctx.session_id) if ctx.session_id else None
            if scene_row is not None:
                dest_npc_refs = [f"npc:{n.id}" for n in (await s.execute(
                    select(NPC).where(NPC.campaign_id == ctx.campaign_id,
                                      NPC.current_location_id == reached_id))).scalars()]
                await SceneService(s).close_scene(scene_row)
                await SceneService(s).create_scene(
                    session_id=scene_row.session_id, location_id=reached_id,
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
        if waypoints:
            # The compressed route: players see they passed THROUGH the exterior, not
            # teleported. Detail is compressed; time and world state still reflect it.
            footer += " · ผ่าน " + " → ".join(waypoints)
        data["footer"] = footer
        responses = [OutboundMessage(ctx.channel_id, text, kind=MessageKind.SCENE_FRAME,
                                     title=sctx.location_name, data=data)]
        note_lines = list(perceivable)
        if blocked_note:
            # The party stopped short — say so, honestly, as part of the arrival.
            note_lines.append(blocked_note)
        if note_lines:
            responses.append(OutboundMessage(
                ctx.channel_id, "\n".join(note_lines), kind=MessageKind.SCENE_TRANSITION,
                title="ระหว่างทาง"))
        note = f"travel -> {sctx.location_name}" + (" (blocked midway)" if blocked_note else "")
        return BridgeResult(handled=True, category=MessageCategory.COMMITTED_ACTION,
                            state_mutated=True, responses=responses, note=note)

    async def _resolve_multi_hop(
        self, ctx, *, current_id: str, reference: str,
    ) -> RoutePlan | str | None:
        """Resolve a movement reference against the WHOLE reachable world.

        Returns a `RoutePlan` (ordered hops) for a named/aliased/NPC-located
        destination; a note string when the reference is ambiguous or the place is
        real but currently unreachable; or None when the reference names no authored
        place (the caller may then expand an ordinary location or clarify)."""
        from app.world.route_service import DestinationClass, RouteService

        async with self.db.session() as read:
            res = await RouteService(read).resolve_destination(
                campaign_id=ctx.campaign_id, from_location_id=current_id, reference=reference)

        if res.route is not None and res.klass in (
                DestinationClass.EXISTING_ADJACENT, DestinationClass.EXISTING_ROUTED):
            return res.route

        if res.klass is DestinationClass.AMBIGUOUS:
            # Several known places (or NPC whereabouts) answer to that reference —
            # ask one focused question instead of guessing.
            return "มีหลายที่ที่เข้าเค้ากับที่เจ้าพูด บอกให้ชัดกว่านี้หน่อยว่าจะไปที่ไหน"

        if res.klass is DestinationClass.UNREACHABLE and res.target is not None:
            # Infer the minimum connective geography — an exterior link out of the
            # current interior and out of the target toward their shared exterior —
            # then re-route. Deterministic, committed, and persisted; this never
            # routes through an unrelated building (the outside rule).
            async with self.db.unit_of_work() as s:
                rs = RouteService(s)
                await rs.infer_exterior_link(campaign_id=ctx.campaign_id, location_id=current_id)
                await rs.infer_exterior_link(campaign_id=ctx.campaign_id, location_id=res.target.id)
            async with self.db.session() as read:
                route = await RouteService(read).find_route(
                    campaign_id=ctx.campaign_id, from_location_id=current_id,
                    to_location_id=res.target.id)
            if route is not None:
                return route
            # Real place, but no open route right now (a locked gate, a severed graph
            # component) — say so; never fabricate a destination or expand over it.
            return f"{res.target.name} มีอยู่จริง แต่ตอนนี้ยังไปไม่ถึง — ลองหาทางอื่นดู"

        return None   # ORDINARY_EXPANDABLE → the caller decides

    async def _execute_route(
        self, s, *, ctx, hop_ids: list[str], movers: list[str], origin_id: str,
    ) -> "_WalkResult":
        """Walk a route one connection at a time. Each hop is re-validated against the
        live graph, its own time is advanced, and every mover steps one location. A
        blocked hop stops the walk at the last valid location — completed segments and
        elapsed time are preserved; nobody is teleported to the final destination."""
        from app.models.location import Location as _Loc

        reached, elapsed, perceivable, reached_names, blocked_note = origin_id, 0, [], [], ""
        for hid in hop_ids:
            hop = await s.get(LocationConnection, hid)
            if hop is None or hop.access_state != "open":
                label = (hop.label if hop else "") or "ทางข้างหน้า"
                state = _access_th(hop.access_state) if hop else "ผ่านไม่ได้"
                blocked_note = f"ไปต่อไม่ได้ — {label}ตอนนี้{state} จึงหยุดอยู่ที่นี่ก่อน"
                break
            if hop.travel_minutes > 0:                      # advance THIS segment's time
                clock = await WorldClockService(s).advance_time(
                    campaign_id=ctx.campaign_id, minutes=hop.travel_minutes,
                    session_id=ctx.session_id, actor_entity=f"character:{ctx.character_id}")
                perceivable.extend(clock.perceivable_notes)
                elapsed += hop.travel_minutes
            for cid in movers:                              # step every mover one hop
                await PositionService(s).move(
                    character_id=cid, to_location_id=hop.to_location_id,
                    campaign_id=ctx.campaign_id, session_id=ctx.session_id,
                    from_location_id=reached)
            reached = hop.to_location_id
            dest = await s.get(_Loc, reached)
            if dest is not None:
                reached_names.append(dest.name)
        return _WalkResult(reached, elapsed, perceivable,
                           reached_names[:-1] if reached_names else [], blocked_note)

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
