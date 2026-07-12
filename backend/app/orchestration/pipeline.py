"""CommittedActionPipeline — the core vertical slice (§11).

Ordered flow for a `!` action:
  1-6  load player/character, scene, location, visible entities, threats, capabilities
  7    ActionInterpreter -> structured intent
  8    validate referenced targets against scene state
  9    RollNecessityJudge + clarification decision
  10   if clarify -> persist pending action, ask ONE Thai question, stop
  11   adjudicate -> resolution type + mechanic + DC (clamped by engine)
  12   execute deterministic tools (server dice + modifiers + comparison)
  13   ConsequencePlanner -> consequence class + proposed deltas
  14   validate proposed deltas (reject illegal / out-of-authority)
  15-16 commit state deltas + canonical event(s) in ONE transaction
  17   DMNarrator -> Thai narration built from the committed result
  18   send response; 19 update scene / present next decision point

The LLM never rolls, never computes a modifier, never writes state. Every number is
produced in step 12 by the deterministic dice engine.
"""
from __future__ import annotations

from app.ai.jobs import (
    ActionInterpreter,
    AdjudicationJudge,
    ConsequencePlanner,
    DMNarrator,
)
from app.core.ids import SYSTEM_ACTOR, entity_ref, parse_entity_ref
from app.core.logging import get_logger
from app.discord_bridge.dto import BridgeResult, OutboundMessage
from app.models.character import Character
from app.models.enums import (
    ActivePlayState,
    EventType,
    MessageCategory,
    ProcessingStage,
    ResolutionType,
    Visibility,
)
from app.models.processed_message import ProcessedMessage
from app.models.session import Session
from app.entities import EntityContext, SceneEntityDirectory
from app.entities.directory import PLAYER_CHARACTER
from app.orchestration.commitment import CommittedAction
from app.orchestration.context import ResolvedContext
from app.presentation import MessageKind
from app.services.events import EventService
from app.services.scenes import SceneService
from app.services.sessions.session_service import SessionService
from app.tabletop.adjudication import (
    DeltaApplier,
    check_modifier,
    decide_clarification,
    normalize_ability,
    normalize_skill,
    resolve_dc,
)
from app.tabletop.dice import DiceEngine
from app.tabletop.rules import ability_for_skill

log = get_logger(__name__)


class CommittedActionPipeline:
    def __init__(self, db, provider, rng) -> None:
        self.db = db
        self.provider = provider
        self.rng = rng
        self.dice = DiceEngine(rng)
        self.interpreter = ActionInterpreter(provider)
        self.adjudicator = AdjudicationJudge(provider)
        self.consequence = ConsequencePlanner(provider)
        self.narrator = DMNarrator(provider)
        from app.world.travel_service import TravelService

        self.travel = TravelService(db, provider)

    # --- entry points --------------------------------------------------------
    async def handle(self, ctx: ResolvedContext, action: CommittedAction) -> BridgeResult:
        return await self._process(ctx, action.action_text, allow_clarify=True)

    async def resume_clarification(
        self, ctx: ResolvedContext, *, answer_text: str, pending: dict
    ) -> BridgeResult:
        """Resumes whatever is pending for this member: a clarification answer, or
        a dice-ritual click (pending['kind'] == 'check')."""
        if pending.get("kind") == "check":
            return await self._resume_check(ctx, answer_text=answer_text, pending=pending)

        original = pending.get("action_text", "")
        merged = f"{original} ({answer_text})".strip()
        await self._clear_pending(ctx)
        # Do not clarify twice — proceed with the merged intent (assume-and-state).
        return await self._process(ctx, merged, allow_clarify=False)

    async def _clear_pending(self, ctx: ResolvedContext) -> None:
        async with self.db.unit_of_work() as s:
            scene = await SceneService(s).get_active_scene(ctx.session_id)
            if scene is not None:
                await SceneService(s).clear_pending_action(scene)
            session_row = await s.get(Session, ctx.session_id)
            if session_row is not None:
                session_row.active_play_state = ActivePlayState.TABLE_OPEN.value
                session_row.version += 1

    # --- core ----------------------------------------------------------------
    async def _process(self, ctx: ResolvedContext, action_text: str, *, allow_clarify: bool) -> BridgeResult:
        # Steps 1-9: load context, hydrate the present cast, interpret, resolve
        # target mentions to canonical identities, adjudicate (all read-only).
        async with self.db.session() as read:
            scene = await SceneService(read).get_active_scene(ctx.session_id) if ctx.session_id else None
            character = await read.get(Character, ctx.character_id) if ctx.character_id else None
            from app.models.campaign import Campaign

            campaign = await read.get(Campaign, ctx.campaign_id)
            dice_mode = (campaign.config or {}).get("dice_mode", "PLAYER_CLICK") if campaign else "AUTO"

            directory = await SceneEntityDirectory(read).build(
                scene, actor_character_id=ctx.character_id, campaign_id=ctx.campaign_id
            )
            interpretation = await self.interpreter.run(
                read, action_text=action_text, scene=scene, character=character,
                directory=directory,
            )
            # Resolve linguistic mentions ONCE, at the engine boundary.
            resolution = directory.resolve_mentions(interpretation.target_references)

        # Resting is its own domain flow, routed BEFORE adjudication: the numbers
        # come from RestService, never from a generic ability check.
        if interpretation.rest_intent and ctx.session_id:
            return await self._handle_rest(ctx, action_text=action_text, interpretation=interpretation)

        # Movement is its own domain flow: the engine resolves the destination from
        # the world graph (never the narrator). No dice, no invented scenery.
        # FOLLOW_SOURCE/LOCAL_MOVEMENT stay in the current Location (adjudicated
        # normally below) — "follow that sound" must never reach WorldExpansion.
        # Only SEARCH_FOR_PLACE (or the legacy unset "NONE", for callers that only
        # set the old `movement_intent` boolean) may fall back to it; a failed
        # CANONICAL_TRAVEL/RETURN_OR_EXIT match gets a clarification, never a new
        # Location.
        kind = interpretation.movement_kind
        if (interpretation.movement_intent and ctx.session_id
                and kind not in ("FOLLOW_SOURCE", "LOCAL_MOVEMENT", "REST")):
            reference = interpretation.movement_reference or action_text
            allow_expansion = kind in ("SEARCH_FOR_PLACE", "NONE")
            return await self.travel.travel(ctx, reference=reference, allow_expansion=allow_expansion)

        async with self.db.session() as read:
            scene = await SceneService(read).get_active_scene(ctx.session_id) if ctx.session_id else None
            character = await read.get(Character, ctx.character_id) if ctx.character_id else None
            decision = await self.adjudicator.run(
                read, action_text=action_text, interpretation=interpretation,
                scene=scene, character=character, directory=directory,
                resolved_targets=resolution.resolved,
            )

        # Step 10a: ambiguous mention -> one focused clarification (test 8).
        if allow_clarify and resolution.ambiguous:
            mention, cands = resolution.ambiguous[0]
            names = " หรือ ".join(c.canonical_name for c in cands)
            return await self._enter_clarification(
                ctx, action_text, f"“{mention}” หมายถึง {names} คนไหน?"
            )

        # Step 10b: the ONLY named target is a known party member who is not in this
        # scene -> not physically reachable (test 9). Party membership != presence.
        if resolution.not_present and not resolution.resolved:
            names = ", ".join(e.canonical_name for e in resolution.not_present)
            return self._table_note(
                ctx, f"{names} ไม่ได้อยู่ในฉากนี้ตอนนี้ — เอื้อมไม่ถึงตัว"
            )

        # Step 10c: PC AGENCY — the action tries to dictate another player
        # character's voluntary choice. The engine refuses to execute it (tests 4/5).
        pc_targets = [e for e in resolution.resolved if e.entity_type == PLAYER_CHARACTER]
        if interpretation.commands_other_pc and pc_targets:
            return await self._agency_safe_response(ctx, directory.actor, pc_targets)

        # Step 10c2: a voluntary NPC social action (ask/greet/thank/threaten/
        # bargain/tell/request-decision) is routed to NPCSocialService per resolved
        # NPC — never the generic narrator, which must not invent NPC dialogue or
        # facts (Fix 3). Targets come from the SAME resolved-mention list used
        # everywhere else; no first-NPC fallback.
        npc_targets = [e for e in resolution.resolved if e.entity_type != PLAYER_CHARACTER]
        if interpretation.social_intent and npc_targets and ctx.character_id:
            return await self._handle_social(ctx, action_text=action_text, npc_targets=npc_targets)

        # Step 10d: ordinary clarification gate.
        if allow_clarify:
            clarify = decide_clarification(interpretation, decision)
            if clarify.needs_clarification:
                question = clarify.question or self._phrase_missing(interpretation)
                return await self._enter_clarification(ctx, action_text, question)

        # The dice ritual: a visible player check pauses for the player's roll.
        needs_roll = (
            decision.resolution_type not in (ResolutionType.AUTOMATIC_SUCCESS,
                                             ResolutionType.AUTOMATIC_FAILURE)
            and character is not None
        )
        if needs_roll and dice_mode == "PLAYER_CLICK":
            return await self._enter_pending_check(
                ctx, action_text=action_text, interpretation=interpretation,
                decision=decision, character=character,
                resolved_targets=resolution.resolved,
            )

        return await self._resolve_commit_narrate(
            ctx, action_text=action_text, goal=interpretation.goal,
            method=interpretation.method, decision=decision,
            character=character, resolved_targets=resolution.resolved, ritual=False,
        )

    async def _resolve_commit_narrate(
        self, ctx: ResolvedContext, *, action_text: str, goal: str, method: str,
        decision, character: Character | None,
        resolved_targets: list[EntityContext], ritual: bool,
    ) -> BridgeResult:
        # The NPC that a consequence may act on: the explicitly RESOLVED NPC target
        # if the player named one, otherwise the scene's immediate THREAT (the danger
        # being faced — e.g. the guard who notices a failed stealth). Never the first
        # entity by list order.
        npc_targets = [e for e in resolved_targets if e.entity_type != PLAYER_CHARACTER]
        target_ref = npc_targets[0].entity_ref if npc_targets else None
        # Steps 11-12: deterministic resolution (server dice + modifiers + DC).
        check_result, outcome = self._resolve_mechanics(decision, character)

        # Step 13: consequence proposal (typed targets, so an NPC delta can never
        # land on a player character just because a name looked NPC-ish).
        async with self.db.session() as read:
            scene2 = await SceneService(read).get_active_scene(ctx.session_id) if ctx.session_id else None
            reactor_ref = target_ref or self._immediate_threat_npc(scene2)
            consequence = await self.consequence.run(
                read, action_text=action_text, outcome=outcome, scene=scene2,
                target_ref=reactor_ref, resolved_targets=resolved_targets,
            )

        # Steps 14-16: validate deltas + commit state + events atomically.
        rejected: list[tuple] = []
        async with self.db.unit_of_work() as s:
            events = EventService(s)
            scene_row = await SceneService(s).get_active_scene(ctx.session_id) if ctx.session_id else None
            scene_id = scene_row.id if scene_row else None
            actor = entity_ref("character", ctx.character_id) if ctx.character_id else SYSTEM_ACTOR

            await events.record(
                campaign_id=ctx.campaign_id, session_id=ctx.session_id, scene_id=scene_id,
                event_type=EventType.PLAYER_ACTION_COMMITTED, actor_entity=actor,
                target_entities=[t.entity_ref for t in resolved_targets],
                payload={"action_text": action_text, "goal": goal,
                         "method": method, "summary": goal,
                         "targets": [{"ref": t.entity_ref, "type": t.entity_type,
                                      "name": t.canonical_name} for t in resolved_targets]},
                visibility=Visibility.PARTY, narrative_significance=20,
            )
            if check_result is not None:
                await events.record(
                    campaign_id=ctx.campaign_id, session_id=ctx.session_id, scene_id=scene_id,
                    event_type=EventType.ABILITY_CHECK_RESOLVED, actor_entity=actor,
                    mechanical_changes=check_result.as_dict(),
                    payload={"ability": check_result.ability, "skill": check_result.skill,
                             "summary": f"เช็ค {check_result.skill or check_result.ability} -> {outcome}"},
                    visibility=Visibility.PARTY, narrative_significance=15,
                )

            applier = DeltaApplier(
                s, campaign_id=ctx.campaign_id, session_id=ctx.session_id,
                scene_id=scene_id, actor_entity=actor,
            )
            applier.allowed_clues = list(scene_row.allowed_clues or []) if scene_row else []
            _, rejected = await applier.apply_valid(consequence.deltas)
            private_reveals = list(applier.private_reveals)
            fragments = list(applier.revealed_fragments)

            pm = await s.get(ProcessedMessage, ctx.processed_message_id) if ctx.processed_message_id else None
            if pm is not None:
                pm.stage = ProcessingStage.COMMITTED.value
                pm.category = MessageCategory.COMMITTED_ACTION.value
                # Record the factual outcome IN the commit txn so recovery can
                # restate it if narration/delivery later fails (never re-execute).
                pm.result = {
                    "outcome": outcome,
                    "roll_line": self._roll_line(check_result, outcome),
                }

            session_row = await s.get(Session, ctx.session_id) if ctx.session_id else None
            if session_row is not None:
                session_row.active_play_state = ActivePlayState.TABLE_OPEN.value
                session_row.version += 1

            # Spotlight (NOT turn order): remember who just acted + tally, so quiet
            # characters stay in the DM's awareness. Presence/participation/spotlight
            # are distinct (docs/multiplayer-identity.md).
            if scene_row is not None and ctx.character_id:
                self._bump_spotlight(scene_row, entity_ref("character", ctx.character_id))

        if rejected:
            log.warning("dropped %d illegal consequence delta(s): %s",
                        len(rejected), [r[1] for r in rejected])

        # Step 17: narration from the committed result — with typed actor/targets so
        # it never swaps names or makes another player's character act.
        result_summary = self._result_summary(check_result, outcome, decision)
        async with self.db.session() as read:
            scene3 = await SceneService(read).get_active_scene(ctx.session_id) if ctx.session_id else None
            directory3 = await SceneEntityDirectory(read).build(
                scene3, actor_character_id=ctx.character_id, campaign_id=ctx.campaign_id
            )
            from app.memory.scene_context import SceneContextBuilder

            scene_ctx = await SceneContextBuilder(read).build(
                campaign_id=ctx.campaign_id, scene=scene3, actor_character_id=ctx.character_id
            )
            narration = await self.narrator.run(
                read, action_text=action_text, outcome=outcome,
                result_summary=result_summary, scene=scene3, target_ref=target_ref,
                directory=directory3, resolved_targets=resolved_targets,
                scene_context=scene_ctx,
            )
        # Anti-hallucination: never let the DM ask the player to author the world.
        from app.ai.narration_guard import screen_decision_prompt, screen_narration

        actor_name = directory3.actor.canonical_name if directory3.actor else None
        narration.text, _ = screen_narration(narration.text, actor_name)
        narration.decision_prompt = screen_decision_prompt(narration.decision_prompt, actor_name)

        # Step 18-19: cache response + mark SENT.
        roll_line = self._roll_line(check_result, outcome)
        async with self.db.unit_of_work() as s:
            pm = await s.get(ProcessedMessage, ctx.processed_message_id) if ctx.processed_message_id else None
            if pm is not None:
                pm.stage = ProcessingStage.SENT.value
                pm.result = {"response": narration.text, "outcome": outcome,
                             "roll_line": roll_line}

        fragment_field = (
            [{"name": "ได้ยินมาแว่วๆ", "value": "\n".join(f"“{f}”" for f in fragments),
              "inline": False}] if fragments else []
        )
        if ritual:
            # ROLL and NARRATION are separate presentation objects (§28).
            responses = [
                OutboundMessage(
                    ctx.channel_id, "", kind=MessageKind.CHECK_RESOLUTION,
                    title=self._check_title(check_result),
                    data={"roll_line": roll_line, "outcome": outcome},
                ),
                OutboundMessage(
                    ctx.channel_id, narration.text, kind=MessageKind.SCENE_FRAME,
                    data={"decision_prompt": narration.decision_prompt,
                          "fields": fragment_field} if fragment_field else
                         {"decision_prompt": narration.decision_prompt},
                ),
            ]
        else:
            responses = [OutboundMessage(
                ctx.channel_id, narration.text,
                kind=MessageKind.CHECK_RESOLUTION,
                data={
                    "roll_line": roll_line,
                    "decision_prompt": narration.decision_prompt,
                    "outcome": outcome,
                    **({"fields": fragment_field} if fragment_field else {}),
                },
            )]
        # Engine-enforced private delivery of committed reveals (never public).
        for reveal in private_reveals:
            discord_id = await self._discord_id_for_character(reveal["character_id"])
            if discord_id is not None:
                responses.append(OutboundMessage(
                    ctx.channel_id, reveal["fact"], kind=MessageKind.PRIVATE_SECRET,
                    title="เฉพาะเจ้าเท่านั้นที่รู้", private_to_discord_id=discord_id,
                    data={"footer": "คนอื่นในโต๊ะไม่เห็นข้อความนี้"},
                ))
        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION, state_mutated=True,
            responses=responses,
            note=f"outcome={outcome}; consequence={consequence.consequence_class.value}",
        )

    async def _discord_id_for_character(self, character_id: str) -> str | None:
        from app.models.campaign import CampaignMember
        from app.models.user import User

        async with self.db.session() as s:
            char = await s.get(Character, character_id)
            if char is None:
                return None
            member = await s.get(CampaignMember, char.owner_member_id)
            if member is None:
                return None
            user = await s.get(User, member.user_id)
            return user.discord_user_id if user else None

    # --- social actions: engine-routed, NPC-authorized -------------------------
    async def _handle_social(
        self, ctx: ResolvedContext, *, action_text: str, npc_targets: list[EntityContext],
    ) -> BridgeResult:
        """A voluntary NPC-directed social action. Each resolved NPC answers from
        ITS OWN epistemic + protocol-authorized context (NPCSocialService); the
        generic narrator never touches this — it cannot invent NPC dialogue, rules,
        or facts. No roll: whether a social roll is warranted is a fiction-level
        judgement NPCSocialService/the adjudicator upstream already declined to
        force here (Fix 3)."""
        from app.npcs import NPCSocialService

        # Record the committed social action FIRST so each NPC's episodic memory can
        # link to its canonical source event (the memory loop, §10).
        async with self.db.unit_of_work() as s:
            scene_row0 = await SceneService(s).get_active_scene(ctx.session_id) if ctx.session_id else None
            scene_id0 = scene_row0.id if scene_row0 else None
            actor = entity_ref("character", ctx.character_id)
            source_event = await EventService(s).record(
                campaign_id=ctx.campaign_id, session_id=ctx.session_id, scene_id=scene_id0,
                event_type=EventType.PLAYER_ACTION_COMMITTED, actor_entity=actor,
                target_entities=[e.entity_ref for e in npc_targets],
                payload={"action_text": action_text, "summary": action_text, "social": True},
                visibility=Visibility.PARTY, narrative_significance=15,
            )
            source_event_id = source_event.id

        social_svc = NPCSocialService(self.db, self.provider)
        responses: list[OutboundMessage] = []
        for npc_ec in npc_targets[:4]:
            _, npc_id = parse_entity_ref(npc_ec.entity_ref)
            social = await social_svc.respond(
                campaign_id=ctx.campaign_id, npc_id=npc_id,
                listener_ref=entity_ref("character", ctx.character_id),
                utterance=action_text, session_id=ctx.session_id,
                source_event_id=source_event_id,
            )
            responses.append(OutboundMessage(
                ctx.channel_id, social.utterance, kind=MessageKind.NPC_DIALOGUE,
                title=npc_ec.canonical_name,
            ))

        async with self.db.unit_of_work() as s:
            scene_row = await SceneService(s).get_active_scene(ctx.session_id) if ctx.session_id else None
            scene_id = scene_row.id if scene_row else None
            actor = entity_ref("character", ctx.character_id)
            pm = await s.get(ProcessedMessage, ctx.processed_message_id) if ctx.processed_message_id else None
            if pm is not None:
                pm.stage = ProcessingStage.SENT.value
                pm.category = MessageCategory.COMMITTED_ACTION.value
                pm.result = {"response": responses[0].content if responses else "", "social": True}
            session_row = await s.get(Session, ctx.session_id) if ctx.session_id else None
            if session_row is not None:
                session_row.active_play_state = ActivePlayState.TABLE_OPEN.value
                session_row.version += 1
            if scene_row is not None:
                self._bump_spotlight(scene_row, actor)

        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION, state_mutated=True,
            responses=responses, note="social_intent -> NPCSocialService",
        )

    # --- resting: a real domain operation, never a generic ability check -------
    async def _handle_rest(
        self, ctx: ResolvedContext, *, action_text: str, interpretation,
    ) -> BridgeResult:
        if interpretation.rest_kind == "ambiguous":
            return await self._enter_clarification(
                ctx, action_text, "จะพักสั้นประมาณหนึ่งชั่วโมง หรือพักยาวคืนนี้?")
        if not ctx.character_id:
            return self._table_note(ctx, "ยังไม่รู้ว่าใครกำลังพัก")

        from app.tabletop.rest.rest_service import RestService

        # Actor-only for this slice regardless of rest_scope — a solo player must
        # never be able to silently commit another player's character to sleep
        # (documented limitation; see PROGRESS.md).
        character_ids = [ctx.character_id]
        rest = RestService(self.db, self.rng)
        if interpretation.rest_kind == "short":
            outcome = await rest.short_rest(
                campaign_id=ctx.campaign_id, character_ids=character_ids, session_id=ctx.session_id)
        else:
            outcome = await rest.long_rest(
                campaign_id=ctx.campaign_id, character_ids=character_ids, session_id=ctx.session_id)

        async with self.db.unit_of_work() as s:
            pm = await s.get(ProcessedMessage, ctx.processed_message_id) if ctx.processed_message_id else None
            if pm is not None:
                pm.stage = ProcessingStage.SENT.value
                pm.category = MessageCategory.COMMITTED_ACTION.value
            session_row = await s.get(Session, ctx.session_id) if ctx.session_id else None
            if session_row is not None:
                session_row.active_play_state = ActivePlayState.TABLE_OPEN.value
                session_row.version += 1

        kind_th = "ยาว" if outcome.kind == "long" else "สั้น"
        if not outcome.completed:
            body = (f"การพัก{kind_th}ถูกขัดจังหวะ — ไม่ได้รับประโยชน์จากการพัก\n\n"
                    + "\n".join(f"• {n}" for n in outcome.interrupted_by))
            return BridgeResult(
                handled=True, category=MessageCategory.COMMITTED_ACTION, state_mutated=True,
                responses=[OutboundMessage(ctx.channel_id, body, kind=MessageKind.SCENE_TRANSITION,
                                           title=f"พัก{kind_th}ถูกขัดจังหวะ")],
                note=f"rest={outcome.kind} interrupted",
            )
        lines = [f"{name}: " + ("; ".join(notes) if notes else "ไม่มีอะไรเปลี่ยนแปลง")
                for name, notes in outcome.notes_th.items()]
        body = f"พัก{kind_th}เสร็จสิ้น" + ("\n\n" + "\n".join(lines) if lines else "")
        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION, state_mutated=True,
            responses=[OutboundMessage(ctx.channel_id, body, kind=MessageKind.SCENE_TRANSITION,
                                       title=f"พัก{kind_th}เสร็จสิ้น")],
            note=f"rest={outcome.kind} completed",
        )

    # --- the dice ritual (pending check) --------------------------------------
    ROLL_TRIGGERS = ("ทอย", "🎲", "roll")
    CANCEL_WORDS = ("ยกเลิก", "cancel")

    async def _enter_pending_check(
        self, ctx: ResolvedContext, *, action_text: str, interpretation, decision,
        character: Character, resolved_targets: list[EntityContext],
    ) -> BridgeResult:
        from app.core.ids import new_id

        pending = {
            "id": new_id(), "kind": "check",
            "member_id": ctx.member_id, "character_id": ctx.character_id,
            "action_text": action_text,
            "goal": interpretation.goal, "method": interpretation.method,
            "decision": decision.model_dump(mode="json"),
            # Resolved identities survive the pause so the roll doesn't re-resolve.
            "targets": [t.to_public() for t in resolved_targets],
        }
        async with self.db.unit_of_work() as s:
            scene = await SceneService(s).get_active_scene(ctx.session_id) if ctx.session_id else None
            if scene is not None:
                await SceneService(s).set_pending_action(scene, pending)
            session_row = await s.get(Session, ctx.session_id) if ctx.session_id else None
            if session_row is not None:
                session_row.active_play_state = ActivePlayState.CLARIFICATION_REQUIRED.value
                session_row.version += 1
            pm = await s.get(ProcessedMessage, ctx.processed_message_id) if ctx.processed_message_id else None
            if pm is not None:
                pm.stage = ProcessingStage.ADJUDICATED.value
                pm.category = MessageCategory.COMMITTED_ACTION.value
                pm.pending_action_id = pending["id"]

        label, mod_line = self._check_label_and_mods(decision, character)
        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION, state_mutated=False,
            responses=[OutboundMessage(
                ctx.channel_id, mod_line, kind=MessageKind.CHECK_PROMPT,
                title=label, choices=["🎲 ทอย d20"],
                data={"footer": "โชคชะตาอยู่ในมือเจ้า"},
            )],
            note="pending check (dice ritual)",
        )

    async def _resume_check(
        self, ctx: ResolvedContext, *, answer_text: str, pending: dict
    ) -> BridgeResult:
        text = (answer_text or "").strip()
        low = text.lower()
        if any(w in low for w in self.CANCEL_WORDS):
            await self._clear_pending(ctx)
            return BridgeResult(handled=True, responses=[OutboundMessage(
                ctx.channel_id, "ถอนมือออกมาก่อน — ยังไม่มีอะไรเกิดขึ้น",
                kind=MessageKind.TABLE_NOTICE)])
        if text.startswith("!"):
            # The player changed their mind with a new committed action.
            await self._clear_pending(ctx)
            return await self._process(ctx, text.lstrip("!").strip(), allow_clarify=True)
        if not any(t in low for t in self.ROLL_TRIGGERS):
            return BridgeResult(handled=True, responses=[OutboundMessage(
                ctx.channel_id, "ลูกเต๋ายังรออยู่ — แตะ 🎲 เพื่อทอย หรือพิมพ์ 'ยกเลิก'",
                kind=MessageKind.TABLE_NOTICE)])

        from app.schemas.llm_io import AdjudicationDecision

        decision = AdjudicationDecision.model_validate(pending["decision"])
        resolved_targets = [EntityContext.from_public(t) for t in pending.get("targets", [])]
        async with self.db.session() as read:
            character = await read.get(Character, pending.get("character_id"))
        await self._clear_pending(ctx)
        return await self._resolve_commit_narrate(
            ctx, action_text=pending.get("action_text", ""),
            goal=pending.get("goal", ""), method=pending.get("method", ""),
            decision=decision, character=character,
            resolved_targets=resolved_targets, ritual=True,
        )

    def _check_label_and_mods(self, decision, character: Character) -> tuple[str, str]:
        skill = normalize_skill(decision.skill)
        ability = normalize_ability(decision.ability) or (
            ability_for_skill(skill) if skill else "wis"
        )
        modifier, proficient = check_modifier(character, ability, skill)
        name = skill or ability
        th = self._SKILL_TH.get(name, "")
        label = f"{name.replace('_', ' ').title()}{f' ({th})' if th else ''}"
        prof_note = " · ถนัด" if proficient else ""
        return label, f"{character.name} — โมดิฟายเออร์ {modifier:+d}{prof_note}"

    @classmethod
    def _check_title(cls, check_result) -> str:
        if check_result is None:
            return "ผลการตัดสิน"
        name = check_result.skill or check_result.ability
        th = cls._SKILL_TH.get(name, "")
        return f"{name.replace('_', ' ').title()}{f' ({th})' if th else ''}"

    # --- helpers -------------------------------------------------------------
    async def _enter_clarification(
        self, ctx: ResolvedContext, action_text: str, question: str
    ) -> BridgeResult:
        from app.core.ids import new_id

        pending = {
            "id": new_id(),
            "kind": "clarification",
            "member_id": ctx.member_id,
            "character_id": ctx.character_id,
            "action_text": action_text,
        }
        async with self.db.unit_of_work() as s:
            scene = await SceneService(s).get_active_scene(ctx.session_id) if ctx.session_id else None
            if scene is not None:
                await SceneService(s).set_pending_action(scene, pending)
            session_row = await s.get(Session, ctx.session_id) if ctx.session_id else None
            if session_row is not None:
                session_row.active_play_state = ActivePlayState.CLARIFICATION_REQUIRED.value
                session_row.version += 1
            pm = await s.get(ProcessedMessage, ctx.processed_message_id) if ctx.processed_message_id else None
            if pm is not None:
                pm.stage = ProcessingStage.INTERPRETED.value
                pm.category = MessageCategory.COMMITTED_ACTION.value
                pm.pending_action_id = pending["id"]
                pm.result = {"response": question}

        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION, state_mutated=False,
            responses=[OutboundMessage(ctx.channel_id, question)],
            note="clarification required",
        )

    def _resolve_mechanics(self, decision, character: Character | None):
        """Deterministic. Returns (check_result_or_None, outcome_str).

        Tolerant of real-LLM vocabulary: ability/skill names are normalized, and any
        unexpected value degrades to a safe WIS check rather than crashing the turn.
        """
        rt = decision.resolution_type
        if rt == ResolutionType.AUTOMATIC_SUCCESS:
            return None, "success"
        if rt == ResolutionType.AUTOMATIC_FAILURE:
            return None, "failure"

        if character is None:
            # No character to roll for — degrade safely rather than invent a number.
            return None, "failure"

        # ABILITY_CHECK / SAVING_THROW / ATTACK(out of combat) / SPECIAL all resolve as
        # a d20 check in the MVP subset; ATTACK proper lives in the combat engine.
        try:
            skill = normalize_skill(decision.skill)  # None if unsupported/absent
            ability = normalize_ability(decision.ability)
            if ability is None:
                ability = ability_for_skill(skill) if skill else "wis"
            modifier, proficient = check_modifier(character, ability, skill)
            dc = resolve_dc(decision.dc_band)
            if rt == ResolutionType.SAVING_THROW:
                result = self.dice.resolve_saving_throw(
                    modifier=modifier, dc=dc, ability=ability,
                    advantage=decision.advantage, disadvantage=decision.disadvantage,
                )
            else:
                result = self.dice.resolve_ability_check(
                    modifier=modifier, dc=dc, ability=ability, skill=skill, proficient=proficient,
                    advantage=decision.advantage, disadvantage=decision.disadvantage,
                )
            return result, result.outcome
        except Exception as exc:  # noqa: BLE001 - never crash a turn on odd AI output
            log.warning("mechanics resolution fell back to a WIS check: %s", exc)
            modifier, _ = check_modifier(character, "wis", None)
            result = self.dice.resolve_ability_check(modifier=modifier, dc=15, ability="wis")
            return result, result.outcome

    # --- PC agency + presence + spotlight -------------------------------------
    async def _agency_safe_response(
        self, ctx: ResolvedContext, actor: EntityContext | None,
        pc_targets: list[EntityContext],
    ) -> BridgeResult:
        """The action tried to dictate another player character's voluntary choice.
        Frame the ACTOR's attempt; hand the decision to the other player. No roll,
        no state change — PC agency is inviolable and enforced here in the engine,
        not merely requested of the narrator."""
        actor_name = actor.canonical_name if actor else "ตัวละครของเจ้า"
        names = " และ ".join(t.canonical_name for t in pc_targets)
        controllers = [t.controller_member_id for t in pc_targets if t.controller_member_id]
        body = (
            f"{actor_name} หันไปหา {names} แล้วเอ่ยปากออกไป\n"
            f"…แต่ {names} จะทำอย่างไรต่อ เป็นสิทธิ์ของผู้เล่นที่ควบคุม {names} เอง"
        )
        async with self.db.unit_of_work() as s:
            pm = await s.get(ProcessedMessage, ctx.processed_message_id) if ctx.processed_message_id else None
            if pm is not None:
                pm.stage = ProcessingStage.SENT.value
                pm.category = MessageCategory.COMMITTED_ACTION.value
                pm.result = {"response": body, "agency": "deferred_to_target_player"}
        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION, state_mutated=False,
            responses=[OutboundMessage(
                ctx.channel_id, body, kind=MessageKind.SCENE_FRAME,
                data={"decision_prompt": f"{names} — จะตอบสนองอย่างไร?",
                      "footer": "ตัวละครของผู้เล่นคนอื่นตัดสินใจเอง"},
            )],
            note=f"pc-agency: action over {names} deferred to their player",
        )

    @staticmethod
    def _immediate_threat_npc(scene) -> str | None:
        """The pressing threat that reacts to an untargeted consequence (e.g. who
        notices a failed sneak). Only immediate_threat_ids — a semantic 'the danger',
        NOT generic visible-entity list order."""
        if scene is None:
            return None
        for ref in list(scene.immediate_threat_ids or []):
            if ref.startswith("npc:"):
                return ref
        return None

    def _table_note(self, ctx: ResolvedContext, text: str) -> BridgeResult:
        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION, state_mutated=False,
            responses=[OutboundMessage(ctx.channel_id, text, kind=MessageKind.TABLE_NOTICE)],
            note="target not reachable in this scene",
        )

    @staticmethod
    def _bump_spotlight(scene_row, actor_ref: str) -> None:
        spot = dict(scene_row.spotlight or {})
        counts = dict(spot.get("action_counts") or {})
        counts[actor_ref] = int(counts.get(actor_ref, 0)) + 1
        spot["last_actor"] = actor_ref
        spot["action_counts"] = counts
        scene_row.spotlight = spot
        scene_row.version += 1

    @staticmethod
    def _phrase_missing(interpretation) -> str:
        if interpretation.missing_information:
            return interpretation.missing_information[0]
        return "ช่วยบอกให้ชัดขึ้นอีกนิดได้ไหม"

    # Thai skill display names for the visible dice line.
    _SKILL_TH = {
        "stealth": "ย่องเงียบ", "perception": "สังเกต", "investigation": "สืบค้น",
        "athletics": "กำลังกาย", "acrobatics": "ผาดโผน", "persuasion": "โน้มน้าว",
        "deception": "ลวงหลอก", "intimidation": "ข่มขู่", "insight": "อ่านใจ",
        "survival": "เอาตัวรอด", "medicine": "รักษา", "arcana": "เวทวิทยา",
        "history": "ประวัติศาสตร์", "nature": "ธรรมชาติ", "religion": "ศาสนา",
        "sleight_of_hand": "มือไว", "animal_handling": "สัตว์", "performance": "การแสดง",
    }

    @classmethod
    def _roll_line(cls, check_result, outcome: str) -> str:
        """The player-visible mechanical line, built ONLY from committed numbers."""
        verdict = "สำเร็จ ✓" if outcome == "success" else "พลาด ✗"
        if check_result is None:
            return verdict if outcome == "success" else f"เป็นไปไม่ได้ — {verdict}"
        name = check_result.skill or check_result.ability
        th = cls._SKILL_TH.get(name, "")
        label = f"{name.capitalize()}{f' ({th})' if th else ''}"
        return (
            f"{label}: {check_result.natural_roll} + {check_result.modifier} = "
            f"{check_result.total} vs DC {check_result.dc} — {verdict}"
        )

    @staticmethod
    def _result_summary(check_result, outcome: str, decision) -> str:
        if check_result is None:
            return f"resolution={decision.resolution_type.value}; outcome={outcome}"
        return (
            f"{check_result.skill or check_result.ability}: "
            f"{check_result.natural_roll}+{check_result.modifier}="
            f"{check_result.total} vs DC{check_result.dc} -> {outcome}"
        )
