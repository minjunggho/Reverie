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

from sqlalchemy import select

from app.ai.jobs import (
    ActionInterpreter,
    AdjudicationJudge,
    CheckSetupGenerator,
    ConsequencePlanner,
    DMNarrator,
)
from app.ai.pacing import select_pacing
from app.core.errors import RulesViolation, ValidationError
from app.core.ids import SYSTEM_ACTOR, entity_ref, parse_entity_ref
from app.core.logging import get_logger
from app.discord_bridge.dto import BridgeResult, OutboundMessage
from app.memory.character_context import build_character_narrative_context
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
from app.services.scenes.stall_service import StallService, TurnProgress
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
from app.world.turn_clock import minutes_for_turn
from app.world.world_clock import WorldClockService

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
        self.check_setup = CheckSetupGenerator(provider)
        from app.world.travel_service import TravelService

        self.travel = TravelService(db, provider)

    # --- entry points --------------------------------------------------------
    async def handle(self, ctx: ResolvedContext, action: CommittedAction) -> BridgeResult:
        if action.is_speech:
            return await self._handle_speech(ctx, utterance=action.action_text)
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
    async def _process(self, ctx: ResolvedContext, action_text: str, *, allow_clarify: bool,
                       preset_interpretation=None) -> BridgeResult:
        # Steps 1-9: load context, hydrate the present cast, interpret, resolve
        # target mentions to canonical identities, adjudicate (all read-only).
        # `preset_interpretation` is supplied when the ordered-plan executor
        # dispatches a single step — it skips re-interpretation and the plan/follow
        # branches (no recursion), routing that one step through the normal path.
        async with self.db.session() as read:
            scene = await SceneService(read).get_active_scene(ctx.session_id) if ctx.session_id else None
            character = await read.get(Character, ctx.character_id) if ctx.character_id else None
            from app.models.campaign import Campaign

            campaign = await read.get(Campaign, ctx.campaign_id)
            dice_mode = (campaign.config or {}).get("dice_mode", "PLAYER_CLICK") if campaign else "AUTO"

            directory = await SceneEntityDirectory(read).build(
                scene, actor_character_id=ctx.character_id, campaign_id=ctx.campaign_id
            )
            interpretation = preset_interpretation or await self.interpreter.run(
                read, action_text=action_text, scene=scene, character=character,
                directory=directory,
            )
            # Resolve linguistic mentions ONCE, at the engine boundary.
            resolution = directory.resolve_mentions(interpretation.target_references)

        # Natural following + ordered compound plans are decided ONCE, before the
        # flat single-action routing (skipped for preset steps to avoid recursion).
        if preset_interpretation is None:
            if ((interpretation.follow_intent or interpretation.stop_following)
                    and ctx.character_id and ctx.session_id):
                return await self._handle_follow(ctx, interpretation)
            immediate = [s for s in interpretation.steps if s.temporal == "IMMEDIATE"]
            if len(immediate) >= 2 and ctx.character_id and ctx.session_id:
                from app.orchestration.action_plan import build_plan

                actor = entity_ref("character", ctx.character_id)
                return await self._execute_plan(
                    ctx, build_plan(interpretation, actor_ref=actor), allow_clarify=allow_clarify)

        # Handing an object over is a DOMAIN action: the engine validates possession
        # + authoritative presence and commits the transfer exactly once — narration
        # can only ever describe a hand-over that actually happened.
        if interpretation.give_intent and ctx.character_id and ctx.session_id:
            return await self._handle_give(
                ctx, action_text=action_text, interpretation=interpretation,
                directory=directory, resolution=resolution)

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

        # Spellcasting is its own domain flow: the ENGINE resolves the spell + targets
        # + authoritative stats and runs SpellEngine.cast — the numbers never come
        # from a generic ability check or the narrator. Routed BEFORE adjudication.
        if interpretation.cast_intent and ctx.character_id and ctx.session_id:
            return await self._handle_cast(
                ctx, action_text=action_text, interpretation=interpretation,
                resolved_targets=resolution.resolved)

        # Class-feature activation ("ใช้ Second Wind", "เข้าโหมดเกรี้ยวกราด") — the
        # engine resolves the feature against the character's granted features and
        # spends its resource via ResourceEngine; numbers come from the dice engine.
        if interpretation.activate_intent and ctx.character_id and ctx.session_id:
            return await self._handle_activate(ctx, interpretation=interpretation)

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
        # A coercive/deceptive social action whose outcome is genuinely uncertain
        # (social_uncertain) is NOT free dialogue — it falls through to the normal
        # adjudication/dice-ritual path below, same as any other contested action.
        npc_targets = [e for e in resolution.resolved if e.entity_type != PLAYER_CHARACTER]
        # ENGINE GATE: speaking to someone who is waiting for an explanation is never
        # free dialogue. Caught reaching for the map and then talking about a
        # cockroach is an ATTEMPT to explain, and it has to be contested — otherwise
        # a change of subject is a free pass, which is exactly the reported bug.
        # The model decides `social_uncertain` from the words alone and cannot see the
        # open thread; the engine can, so the engine overrides.
        owed_explanation = await self._has_open_question(ctx, npc_targets)
        if (interpretation.social_intent and npc_targets and ctx.character_id
                and not interpretation.social_uncertain and not owed_explanation):
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
            object_name=interpretation.object_reference,
        )

    async def _resolve_commit_narrate(
        self, ctx: ResolvedContext, *, action_text: str, goal: str, method: str,
        decision, character: Character | None,
        resolved_targets: list[EntityContext], ritual: bool,
        object_name: str = "",
    ) -> BridgeResult:
        # The NPC that a consequence may act on: the explicitly RESOLVED NPC target
        # if the player named one, otherwise the scene's immediate THREAT (the danger
        # being faced — e.g. the guard who notices a failed stealth). Never the first
        # entity by list order.
        npc_targets = [e for e in resolved_targets if e.entity_type != PLAYER_CHARACTER]
        target_ref = npc_targets[0].entity_ref if npc_targets else None
        # Steps 11-12: deterministic resolution (server dice + modifiers + DC), plus
        # any dice active effects owe this roll (Guidance). The grants are resolved
        # from committed state, never from the narration or the model.
        bonus_grants = await self._bonus_grants_for_check(ctx, decision, character)
        composed_dc = await self._compose_dc(ctx, decision, resolved_targets)
        check_result, outcome = self._resolve_mechanics(
            decision, character, bonus_grants, composed_dc)

        # The scene this action STARTED from. Captured before any commit so the
        # diagnostic below can prove an ordinary action continued the scene it found,
        # rather than silently landing in a new one.
        async with self.db.session() as read:
            scene_before = (await SceneService(read).get_active_scene(ctx.session_id)
                            if ctx.session_id else None)
        scene_id_before = scene_before.id if scene_before else None
        scene_version_before = scene_before.version if scene_before else None

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

            # WHO the world saw do this, and what they made of it. Resolved BEFORE the
            # event is recorded so the ledger carries the witness list rather than
            # leaving `Event.witnesses` empty, as it always has on this path.
            witnessed = await self._witness_action(
                s, ctx, decision=decision, outcome=outcome, scene_row=scene_row,
                character=character, object_name=object_name,
                resolved_targets=resolved_targets)

            action_event = await events.record(
                campaign_id=ctx.campaign_id, session_id=ctx.session_id, scene_id=scene_id,
                event_type=EventType.PLAYER_ACTION_COMMITTED, actor_entity=actor,
                target_entities=[t.entity_ref for t in resolved_targets],
                location_id=scene_row.location_id if scene_row else None,
                witnesses=witnessed.witnesses if witnessed else [],
                payload={"action_text": action_text, "goal": goal,
                         "method": method, "summary": goal,
                         "action_class": witnessed.action_class if witnessed else None,
                         "detection": witnessed.detection if witnessed else None,
                         "outcome": outcome,
                         "targets": [{"ref": t.entity_ref, "type": t.entity_type,
                                      "name": t.canonical_name} for t in resolved_targets]},
                visibility=Visibility.PARTY, narrative_significance=20,
            )
            # The memory loop: every witness now REMEMBERS this, in the same store the
            # NPC decision path reads. Without this the action is invisible to every
            # NPC that watched it happen.
            witness_result = await self._record_witness_memories(
                s, ctx, witnessed=witnessed, event_id=action_event.id,
                scene_row=scene_row, character=character)
            # If this action was an attempt to explain an open thread, settle it here —
            # in the same transaction as the roll that decided whether it was believed.
            settled = await self._settle_open_questions(
                s, ctx, npc_targets=npc_targets, outcome=outcome, decision=decision)
            if check_result is not None:
                await events.record(
                    campaign_id=ctx.campaign_id, session_id=ctx.session_id, scene_id=scene_id,
                    event_type=EventType.ABILITY_CHECK_RESOLVED, actor_entity=actor,
                    mechanical_changes=check_result.as_dict(),
                    payload={"ability": check_result.ability, "skill": check_result.skill,
                             "summary": f"เช็ค {check_result.skill or check_result.ability} -> {outcome}",
                             # How this DC was arrived at — the record of why the
                             # number was what it was, for the log and for later
                             # narration that wants to know the world pushed back.
                             "dc": composed_dc.as_dict() if composed_dc else None},
                    visibility=Visibility.PARTY, narrative_significance=15,
                )

            # A consumed buff is spent HERE, in the same transaction as the roll it
            # paid for — so a roll that never commits never spends the die.
            await self._consume_used_grants(s, check_result, bonus_grants)

            applier = DeltaApplier(
                s, campaign_id=ctx.campaign_id, session_id=ctx.session_id,
                scene_id=scene_id, actor_entity=actor,
            )
            applier.allowed_clues = list(scene_row.allowed_clues or []) if scene_row else []
            applier.allowed_quest_keys = await self._authored_quest_keys(s, ctx.campaign_id)
            applied_events, rejected = await applier.apply_valid(consequence.deltas)
            private_reveals = list(applier.private_reveals)
            fragments = list(applier.revealed_fragments)

            # Did this turn accomplish anything the campaign tracks? Decided from
            # committed state only — never from the narrator's opinion of the fiction.
            progress = TurnProgress(
                clue_opened=any(e.opened_anything for e in applier.clue_effects),
                chapter_moved=bool(applier.chapter_advances),
                objective_moved=any(
                    d.kind == "update_quest" for d in consequence.deltas
                ) and not rejected,
                world_changed=bool(applied_events),
                secret_revealed=bool(applier.private_reveals),
            )
            stall = StallService.record(scene_row, progress)

            # A party going in circles no longer freezes the world. Travel and rest
            # already charge for productive play; this covers the case they never did —
            # standing in one room repeating low-progress actions, where the clock never
            # moved and the consequence engine could never fire. Pressure then arrives
            # because time passed and the world's own scheduled beats came due, not
            # because the DM announced it.
            minutes = minutes_for_turn(
                scene_mode=scene_row.mode if scene_row else None,
                stalled=stall.stalled,
            )
            if minutes > 0:
                await WorldClockService(s).advance_time(
                    campaign_id=ctx.campaign_id, minutes=minutes,
                    session_id=ctx.session_id, scene_id=scene_id, actor_entity=actor,
                )

            pm = await s.get(ProcessedMessage, ctx.processed_message_id) if ctx.processed_message_id else None
            if pm is not None:
                pm.stage = ProcessingStage.COMMITTED.value
                pm.category = MessageCategory.COMMITTED_ACTION.value
                # Record the factual outcome IN the commit txn so recovery can
                # restate it if narration/delivery later fails (never re-execute).
                pm.result = {
                    "outcome": outcome,
                    "roll_line": self._roll_line(check_result, outcome, composed_dc),
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

        # Scene continuity, per committed action. An ordinary action must CONTINUE the
        # scene it found; `scene_id_before` != `scene_id_after` on a plain action is
        # the signature of the reported "the story restarted" bug, and this is the
        # line that makes it visible in a log rather than only in a screenshot.
        if scene_id_before and scene_id != scene_id_before:
            log.warning(
                "scene changed during an ordinary action",
                extra={"campaign_id": ctx.campaign_id, "session_id": ctx.session_id,
                       "channel_id": ctx.channel_id, "user_id": ctx.member_id,
                       "discord_message_id": ctx.inbound.discord_message_id,
                       "scene_id_before": scene_id_before, "scene_id_after": scene_id,
                       "action_text": action_text})
        log.info(
            "action committed",
            extra={"campaign_id": ctx.campaign_id, "session_id": ctx.session_id,
                   "channel_id": ctx.channel_id, "user_id": ctx.member_id,
                   "discord_message_id": ctx.inbound.discord_message_id,
                   "processed_message_id": ctx.processed_message_id,
                   "scene_id_before": scene_id_before, "scene_id_after": scene_id,
                   "scene_version_before": scene_version_before,
                   "outcome": outcome, "ritual": ritual,
                   "bonus_dice": [b.label for b in
                                  (getattr(check_result, "bonus_dice", None) or [])],
                   "dc_band": str(composed_dc.band) if composed_dc else None,
                   "dc_base": composed_dc.base if composed_dc else None,
                   "dc_total": composed_dc.total if composed_dc else None,
                   "dc_factors": [f.key for f in composed_dc.factors]
                                 if composed_dc else [],
                   # The continuity record: what the world saw, and who now
                   # remembers it. An action that should have been memorable but
                   # recorded no witnesses is visible here.
                   "action_class": witnessed.action_class if witnessed else None,
                   "detection": witnessed.detection if witnessed else None,
                   "witnesses": witnessed.witnesses if witnessed else [],
                   "memory_type": witness_result.memory_type if witness_result else None,
                   "open_questions": witness_result.open_questions
                                     if witness_result else [],
                   "questions_settled": settled})

        # Step 17: narration from the committed result — with typed actor/targets so
        # it never swaps names or makes another player's character act. The narrator
        # ALSO receives the full authorized consequence context (class + hint), the
        # engine-selected pacing tier, and the bounded character narrative context —
        # never proposed deltas, only what was actually validated and committed above.
        result_summary = self._result_summary(check_result, outcome, decision)
        async with self.db.session() as read:
            scene3 = await SceneService(read).get_active_scene(ctx.session_id) if ctx.session_id else None
            directory3 = await SceneEntityDirectory(read).build(
                scene3, actor_character_id=ctx.character_id, campaign_id=ctx.campaign_id
            )
            from app.memory.progression_context import ProgressionContextBuilder
            from app.memory.scene_context import SceneContextBuilder

            # NOTE: pressure is deliberately NOT pulled into narration when stalled.
            # SceneContext.pressure_block carries threats' next_action and progress —
            # DM planning material, and this prompt produces player-facing prose. The
            # world leans in through the CLOCK instead: a stalled turn costs more
            # minutes, the clock fires the threats and faction beats that were already
            # scheduled, and those land as committed events. Pressure the party feels is
            # pressure that actually happened.
            stall_state = StallService.state(scene3)
            scene_ctx = await SceneContextBuilder(read).build(
                campaign_id=ctx.campaign_id, scene=scene3,
                actor_character_id=ctx.character_id,
            )
            # The campaign's direction, on EVERY turn — not just the Session 1 prologue.
            progression_ctx = await ProgressionContextBuilder(read).build(
                campaign_id=ctx.campaign_id
            )
            target_name = npc_targets[0].canonical_name if npc_targets else ""
            character_context = await build_character_narrative_context(
                read, character=character, action_text=action_text,
                target_name=target_name, location_name=scene_ctx.location_name,
                threat_name=target_name, consequence_hint=consequence.narration_hint,
                is_saving_throw=decision.resolution_type == ResolutionType.SAVING_THROW,
                campaign_id=ctx.campaign_id,
            )
            critical = bool(check_result and check_result.natural_roll in (1, 20))
            pacing = select_pacing(
                resolution_type=decision.resolution_type,
                consequence_class=consequence.consequence_class,
                is_saving_throw=decision.resolution_type == ResolutionType.SAVING_THROW,
                critical=critical,
                scene_mode=scene3.mode if scene3 else None,
                hook_connected=bool(character_context.relevant_hooks),
            )
            # What the table saw last turn — carried into the packet so this turn
            # CONTINUES the scene rather than restating it (kept as scene working-state).
            previous_narration = (scene3.spotlight or {}).get("last_narration") if scene3 else None
            narration = await self.narrator.run(
                read, action_text=action_text, outcome=outcome,
                result_summary=result_summary, scene=scene3, target_ref=target_ref,
                directory=directory3, resolved_targets=resolved_targets,
                scene_context=scene_ctx, pacing=pacing,
                consequence_class=consequence.consequence_class,
                narration_hint=consequence.narration_hint,
                character_context=character_context,
                progression_context=progression_ctx,
                stall_state=stall_state,
                previous_narration=previous_narration,
            )
        # Anti-hallucination: never let the DM ask the player to author the world.
        from app.ai.narration_guard import is_repeat_narration, screen_decision_prompt, screen_narration

        actor_name = directory3.actor.canonical_name if directory3.actor else None
        narration.text, _ = screen_narration(narration.text, actor_name)
        narration.decision_prompt = screen_decision_prompt(narration.decision_prompt, actor_name)
        # Observability: if the narrator restated last turn's paragraph despite being
        # given it, surface that in the log rather than silently shipping a repeat.
        if is_repeat_narration(previous_narration, narration.text):
            log.warning("narration repeats the previous beat",
                        extra={"campaign_id": ctx.campaign_id, "session_id": ctx.session_id,
                               "scene_id_before": scene_id_before})

        # Step 18-19: cache response + mark SENT — and COMMIT any entities the
        # narration introduces, in the same transaction, BEFORE delivery. A narrated
        # "infected woman" becomes a real NPC at this scene's location, listed in the
        # scene's visible entities, so approaching her next turn resolves instead of
        # looping on "which direction?". Nothing already present is duplicated.
        roll_line = self._roll_line(check_result, outcome, composed_dc)
        async with self.db.unit_of_work() as s:
            if narration.introduced_npcs:
                await self._commit_introduced_npcs(
                    s, ctx=ctx, introduced=narration.introduced_npcs)
            # Remember what the table just saw so next turn's packet can continue it
            # rather than repeat it (scene working-state; see Scene.spotlight).
            scene_now = await SceneService(s).get_active_scene(ctx.session_id) if ctx.session_id else None
            if scene_now is not None:
                scene_now.spotlight = {**(scene_now.spotlight or {}),
                                       "last_narration": narration.text}
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

    @staticmethod
    async def _authored_quest_keys(session, campaign_id: str) -> list[str]:
        """The objective keys this campaign declared. Gates `update_quest` so the model
        can advance an authored objective but never invent one."""
        from sqlalchemy import select

        from app.models.consequences import Quest

        return list((await session.execute(
            select(Quest.key).where(Quest.campaign_id == campaign_id)
        )).scalars().all())

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

    # --- speech: verbatim dialogue, never executed as an action ----------------
    async def _handle_speech(self, ctx: ResolvedContext, *, utterance: str) -> BridgeResult:
        """A quoted `!"..."` message: the player's character SPEAKS these exact words.

        The words are carried verbatim — never re-interpreted into a physical action
        and never reworded by the narrator (so `!"ข้าชักดาบ"` is the character SAYING
        that, not drawing a weapon). If the line addresses NPC(s) present in the
        scene, each answers from its OWN authorized context via NPCSocialService;
        otherwise the line is simply spoken into the scene with no dice and no
        invented reaction — the strongest anti-hallucination guarantee for dialogue.
        """
        if ctx.session_id is None:
            return self._table_note(ctx, "ยังไม่ได้เริ่มเซสชัน เริ่มก่อนถึงจะพูดในฉากได้")

        async with self.db.session() as read:
            scene = await SceneService(read).get_active_scene(ctx.session_id)
            character = await read.get(Character, ctx.character_id) if ctx.character_id else None
            directory = await SceneEntityDirectory(read).build(
                scene, actor_character_id=ctx.character_id, campaign_id=ctx.campaign_id
            )
            # Interpret ONLY to find who is addressed; the spoken words stay verbatim
            # and every action-shaped intent the interpreter reports is ignored.
            interpretation = await self.interpreter.run(
                read, action_text=utterance, scene=scene, character=character,
                directory=directory,
            )
            resolution = directory.resolve_mentions(interpretation.target_references)

        npc_targets = [e for e in resolution.resolved if e.entity_type != PLAYER_CHARACTER]
        # Implicit sole addressee: a line with no named target, spoken where exactly
        # one NPC is present, is addressed to that NPC ("How much for the sword?").
        if not npc_targets and len(directory.present_npcs) == 1:
            npc_targets = list(directory.present_npcs)

        if npc_targets and ctx.character_id:
            return await self._handle_social(ctx, action_text=utterance, npc_targets=npc_targets)
        return await self._speak_into_scene(ctx, utterance=utterance, directory=directory)

    async def _speak_into_scene(
        self, ctx: ResolvedContext, *, utterance: str, directory,
    ) -> BridgeResult:
        """No NPC is addressed (talking to the party, or to the air). Record the
        spoken line as a canonical event and echo it verbatim. No dice, no NPC
        dialogue, no invented consequence — the DM must not answer for anyone."""
        async with self.db.unit_of_work() as s:
            scene_row = await SceneService(s).get_active_scene(ctx.session_id) if ctx.session_id else None
            scene_id = scene_row.id if scene_row else None
            actor = (
                entity_ref("character", ctx.character_id) if ctx.character_id else SYSTEM_ACTOR
            )
            await EventService(s).record(
                campaign_id=ctx.campaign_id, session_id=ctx.session_id, scene_id=scene_id,
                event_type=EventType.PLAYER_ACTION_COMMITTED, actor_entity=actor,
                target_entities=[],
                payload={"action_text": utterance, "summary": utterance,
                         "social": True, "speech": True},
                visibility=Visibility.PARTY, narrative_significance=10,
            )
            pm = await s.get(ProcessedMessage, ctx.processed_message_id) if ctx.processed_message_id else None
            if pm is not None:
                pm.stage = ProcessingStage.SENT.value
                pm.category = MessageCategory.COMMITTED_ACTION.value
                pm.result = {"response": utterance, "speech": True}
            session_row = await s.get(Session, ctx.session_id) if ctx.session_id else None
            if session_row is not None:
                session_row.active_play_state = ActivePlayState.TABLE_OPEN.value
                session_row.version += 1
            if scene_row is not None and ctx.character_id:
                self._bump_spotlight(scene_row, actor)

        speaker = directory.actor.canonical_name if directory.actor else "ตัวละครของเจ้า"
        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION, state_mutated=True,
            responses=[OutboundMessage(
                ctx.channel_id, f"“{utterance}”", kind=MessageKind.NPC_DIALOGUE,
                title=speaker, data={"decision_prompt": "จะทำอะไรต่อ?"},
            )],
            note="speech: spoken into scene (no NPC addressed)",
        )

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

    # --- ordered compound plan: steps executed IN ORDER ------------------------
    async def _execute_plan(self, ctx: ResolvedContext, plan, *, allow_clarify: bool) -> BridgeResult:
        """Execute an ordered plan step by step. Each step routes through the SAME
        single-action pipeline (no parallel mechanics). State commits between steps,
        so a later step sees the world an earlier one left — and an earlier step's
        consequence can PREVENT a later one: a physical step that could not happen
        (movement blocked, target unreachable, a check that mattered failed) halts
        the chain, and the remaining steps are reported as not attempted. FUTURE /
        FLAVOR steps are already excluded (they are intent, not action)."""
        from app.orchestration.action_plan import step_to_interpretation

        responses: list[OutboundMessage] = []
        executed = 0
        halted_at: str | None = None
        for step in plan.executable_steps:
            step_interp = step_to_interpretation(step)
            result = await self._process(
                ctx, step.text or step.method or step.kind,
                allow_clarify=False, preset_interpretation=step_interp)
            responses.extend(result.responses)
            executed += 1
            # Interruption: a physical step that produced no state change (blocked /
            # unreachable / auto-failure) stops the sequence — earlier consequences
            # prevented the rest.
            physical = step.kind in ("MOVE", "ATTACK", "CAST", "INTERACT", "SEARCH",
                                     "HIDE", "USE_ITEM", "TRANSFER_ITEM", "TRANSFER_CURRENCY")
            if physical and not result.state_mutated:
                halted_at = step.kind
                break
        remaining = len(plan.executable_steps) - executed
        if halted_at and remaining > 0:
            responses.append(OutboundMessage(
                ctx.channel_id,
                f"…เหตุการณ์ก่อนหน้าทำให้ทำขั้นต่อไปไม่ได้ ({remaining} ขั้นที่เหลือถูกยกเลิก)",
                kind=MessageKind.TABLE_NOTICE))
        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION,
            state_mutated=executed > 0, responses=responses,
            note=f"ordered plan: {executed}/{len(plan.executable_steps)} steps"
                 + (f", halted at {halted_at}" if halted_at else ""))

    # --- class-feature activation: engine-resolved, shared systems -------------
    async def _handle_activate(self, ctx: ResolvedContext, *, interpretation) -> BridgeResult:
        """Activate a class feature the character HAS. The feature name resolves
        against the granted features by an exact/normalized match; its resource is
        spent atomically; its committed effect (heal / stance / extra action) is
        applied with the shared systems. Invalid or exhausted → nothing spent, a
        Thai diagnostic."""
        from app.entities.directory import normalize_name
        from app.tabletop.classes.features import ClassFeatureService

        reference = (interpretation.feature_reference or "").strip()
        async with self.db.session() as read:
            character = await read.get(Character, ctx.character_id)
            svc = ClassFeatureService(read, self.dice)
            available = await svc.granted_feature_keys(character.id)
        # Resolve the reference to a granted feature key (exact key, or normalized
        # display-name match) — never activate a feature the character lacks.
        key = reference.lower().replace(" ", "_")
        if key not in available:
            norm = normalize_name(reference)
            key = next((k for k in available
                        if norm in normalize_name(k.replace("_", " "))
                        or norm in normalize_name(self.reg_feature_name(character.char_class, k))),
                       None) if reference else None
        if key is None or key not in available:
            return self._table_note(
                ctx, f"{character.name} ไม่มีความสามารถ “{reference}” ที่ใช้ได้ตอนนี้")
        try:
            async with self.db.unit_of_work() as s:
                char = await s.get(Character, ctx.character_id)
                outcome = await ClassFeatureService(s, self.dice).activate(char, key)
                pm = await s.get(ProcessedMessage, ctx.processed_message_id) if ctx.processed_message_id else None
                if pm is not None:
                    pm.stage = ProcessingStage.SENT.value
                    pm.category = MessageCategory.COMMITTED_ACTION.value
                    pm.result = {"response": outcome.line_th, "feature": key}
                session_row = await s.get(Session, ctx.session_id) if ctx.session_id else None
                if session_row is not None:
                    session_row.active_play_state = ActivePlayState.TABLE_OPEN.value
                    session_row.version += 1
        except (RulesViolation, ValidationError) as exc:
            return self._table_note(ctx, f"ใช้ความสามารถนั้นไม่ได้: {exc}")
        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION, state_mutated=True,
            responses=[OutboundMessage(
                ctx.channel_id, outcome.line_th, kind=MessageKind.CHECK_RESOLUTION,
                title=outcome.name_th,
                data={"roll_line": outcome.line_th, "decision_prompt": "จะทำอะไรต่อ?",
                      "outcome": "success"})],
            note=f"activate {key}")

    def reg_feature_name(self, char_class: str, key: str) -> str:
        from app.rules_content import get_registry

        cls = get_registry().get_class(char_class)
        f = next((x for x in cls.features if x.key == key), None)
        return f.name_th if f else key

    # --- natural following: reuse the consent/follow system --------------------
    async def _handle_follow(self, ctx: ResolvedContext, interpretation) -> BridgeResult:
        """'ฉันตาม Kael ไป' / 'ฉันหยุดตาม' — set or clear explicit travel consent via
        PositionService (the SAME system `!rv follow` uses). Following requires
        co-location; a character only ever changes ITS OWN follow state."""
        from app.world import PositionService

        if interpretation.stop_following:
            async with self.db.unit_of_work() as s:
                await PositionService(s).stop_follow(follower_id=ctx.character_id)
                actor = await s.get(Character, ctx.character_id)
                name = actor.name if actor else "ตัวละครของเจ้า"
            return self._table_note(ctx, f"{name} หยุดตามแล้ว — จะอยู่ที่นี่")

        # Resolve the leader: (1) a named scene entity; (2) a party-wide name match —
        # the leader may have JUST left the room (Discord messages are sequential, so
        # "B follows A" often arrives after A moved); (3) the obvious interpretation —
        # exactly ONE other teammate standing right here.
        from app.world.travel_service import TravelService as _TS

        async with self.db.session() as read:
            scene = await SceneService(read).get_active_scene(ctx.session_id)
            directory = await SceneEntityDirectory(read).build(
                scene, actor_character_id=ctx.character_id, campaign_id=ctx.campaign_id)
            resolution = directory.resolve_mentions(
                [interpretation.follow_reference] if interpretation.follow_reference else [])
            leaders = [e for e in resolution.resolved if e.entity_type == PLAYER_CHARACTER
                       and e.entity_ref != entity_ref("character", ctx.character_id)]
            leader_id = None
            if leaders:
                _, leader_id = parse_entity_ref(leaders[0].entity_ref)
            if leader_id is None and interpretation.follow_reference:
                found = await _TS.resolve_party_character(
                    read, campaign_id=ctx.campaign_id,
                    reference=interpretation.follow_reference,
                    exclude_id=ctx.character_id)
                leader_id = found.id if found is not None else None
            if leader_id is None:
                me = await read.get(Character, ctx.character_id)
                here = []
                if me is not None and me.location_id:
                    here = [c for c in await PositionService(read).co_located(
                        campaign_id=ctx.campaign_id, location_id=me.location_id)
                        if c.id != me.id]
                if len(here) == 1:
                    leader_id = here[0].id
        if leader_id is None:
            return self._table_note(
                ctx, "จะตามใคร? บอกชื่อตัวละครที่อยู่ด้วยกันตอนนี้")

        async with self.db.unit_of_work() as s:
            leader = await s.get(Character, leader_id)
            me = await s.get(Character, ctx.character_id)
            if leader is None or me is None:
                return self._table_note(ctx, "จะตามใคร? บอกชื่อตัวละครที่อยู่ด้วยกันตอนนี้")
            leader_loc, my_loc = leader.location_id, me.location_id
            leader_name, actor_name = leader.name, me.name
            if leader_loc == my_loc or leader_loc:
                # Following is consent to travel together — record it either way.
                await PositionService(s).set_follow(follower_id=me.id, leader_id=leader_id)
        if leader_loc == my_loc:
            return self._table_note(
                ctx, f"{actor_name} จะเดินทางตาม {leader_name} ตราบใดที่ยังอยู่ด้วยกัน")
        if leader_loc:
            # The leader already moved on — catch up transactionally: walk the
            # follower to the leader's location (never ask "which direction?").
            return await self.travel.travel(
                ctx, reference=interpretation.follow_reference or leader_name,
                allow_expansion=False, forced_destination_id=leader_loc,
                preserve_follow=True)
        return self._table_note(
            ctx, f"ตอนนี้ไม่มีใครรู้ว่า {leader_name} อยู่ที่ไหน — ต้องหาเบาะแสก่อน")

    async def _commit_introduced_npcs(self, s, *, ctx: ResolvedContext, introduced) -> None:
        """Create the entities a narration introduces — BEFORE the prose reaches the
        table. Bounded (max 3 per narration), campaign-scoped, deduplicated against
        entities already at this location (a Discord retry or a re-mention must never
        spawn a twin). Each NPC lands at the scene's canonical location and joins the
        scene's visible entities, so the directory resolves it next turn."""
        from app.entities.directory import normalize_name
        from app.models.npc import NPC as _NPC
        from app.npcs import NPCService

        scene_row = await SceneService(s).get_active_scene(ctx.session_id) if ctx.session_id else None
        if scene_row is None or not scene_row.location_id:
            return
        existing = list((await s.execute(select(_NPC).where(
            _NPC.campaign_id == ctx.campaign_id,
            _NPC.current_location_id == scene_row.location_id))).scalars())
        taken = {normalize_name(n.name) for n in existing}
        visible = list(scene_row.visible_entity_ids or [])
        for spec in introduced[:3]:
            name = (spec.name or "").strip()
            if not name or normalize_name(name) in taken:
                continue
            npc = await NPCService(s).create_npc(
                campaign_id=ctx.campaign_id, name=name,
                personality=(spec.descriptor or "").strip(),
                current_location_id=scene_row.location_id)
            taken.add(normalize_name(name))
            ref = f"npc:{npc.id}"
            if ref not in visible:
                visible.append(ref)
        scene_row.visible_entity_ids = visible

    # --- give/hand-over: engine-committed ownership, never narrated prose -------
    async def _handle_give(
        self, ctx: ResolvedContext, *, action_text: str, interpretation,
        directory, resolution,
    ) -> BridgeResult:
        """'ส่งขวดให้ Bront' → InventoryService.transfer. The receiver comes from
        authoritative scene presence (named present teammate, else the only other
        teammate here); the item is matched against the sender's REAL inventory; the
        commit is exactly-once on the Discord message id. A refusal states the real
        in-world reason — never a vague failure or a fabricated hand-over."""
        from app.core.errors import ValidationError as _VErr
        from app.entities.directory import normalize_name
        from app.services.campaigns.inventory_service import InventoryService

        # 1. The receiver — a present teammate, from the authoritative directory.
        pcs = [e for e in directory.present_player_characters if not e.is_actor]
        target = None
        wanted = normalize_name(interpretation.give_target_reference or "")
        if wanted:
            named = [e for e in pcs if wanted in e.names_normalized()]
            if len(named) == 1:
                target = named[0]
        if target is None:
            hits = [e for e in resolution.resolved
                    if e.entity_type == PLAYER_CHARACTER and not e.is_actor]
            if len(hits) == 1:
                target = hits[0]
        if target is None and len(pcs) == 1:
            target = pcs[0]                      # the obvious interpretation
        if target is None:
            if resolution.not_present:
                name = resolution.not_present[0].canonical_name
                return self._table_note(
                    ctx, f"{name} ไม่ได้อยู่ตรงนี้ตอนนี้ — "
                         "ต้องอยู่ที่เดียวกันจึงจะส่งของให้กันได้")
            return self._table_note(ctx, "จะส่งให้ใคร? บอกชื่อคนที่อยู่ตรงนี้ด้วยกัน")
        _, receiver_id = parse_entity_ref(target.entity_ref)

        # 2. The item — matched against what the sender actually carries.
        item_ref = (interpretation.give_item_reference or "").strip()
        norm_ref = normalize_name(item_ref)
        async with self.db.session() as read:
            rows = await InventoryService(read).list_inventory(ctx.character_id)
        scored: list[tuple[int, str]] = []
        for _entry, item in rows:
            n = normalize_name(item.name)
            if n and norm_ref and (n in norm_ref or norm_ref in n):
                scored.append((len(n), item.name))
        best_name = item_ref
        if scored:
            scored.sort(reverse=True)
            top = sorted({nm for ln, nm in scored if ln == scored[0][0]})
            if len(top) == 1:
                best_name = top[0]
            else:
                return await self._enter_clarification(
                    ctx, action_text, f"หมายถึงชิ้นไหน: {', '.join(top)}?")

        # 3. Commit exactly once (idempotent on the inbound message id).
        try:
            async with self.db.unit_of_work() as s:
                await InventoryService(s).transfer(
                    from_character_id=ctx.character_id, to_character_id=receiver_id,
                    name=best_name, session_id=ctx.session_id,
                    idempotency_key=ctx.processed_message_id or None)
                me = await s.get(Character, ctx.character_id)
                actor_name = me.name if me else "ตัวละครของเจ้า"
        except _VErr as exc:
            return self._table_note(ctx, str(exc))
        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION,
            state_mutated=True, note="item transferred",
            responses=[OutboundMessage(
                ctx.channel_id,
                f"{actor_name} ส่ง{best_name}ให้ {target.canonical_name} — "
                f"ตอนนี้ของอยู่กับ {target.canonical_name} แล้ว",
                kind=MessageKind.TABLE_NOTICE)])

    # --- spellcasting: engine-resolved, never a generic check ------------------
    async def _handle_cast(
        self, ctx: ResolvedContext, *, action_text: str, interpretation,
        resolved_targets: list[EntityContext],
    ) -> BridgeResult:
        """A real CAST action → SpellEngine.cast. The engine resolves the spell (via
        the authoritative spell resolver, against the caster's OWN known/prepared
        pool), the targets (from the scene), and every number (from stored stats +
        the dice engine). Invalid casts consume nothing. Damage/healing/slot/
        concentration/event all commit in one transaction; the LLM only narrates."""
        from app.rules_content import get_registry
        from app.tabletop.spellcasting import SpellEngine, spellcasting_profile

        reg = get_registry()
        # 1. Resolve the spell against the CASTER'S castable pool (cantrips + prepared).
        async with self.db.session() as read:
            character = await read.get(Character, ctx.character_id)
            profile = await spellcasting_profile(read, character)
            if not profile.is_caster:
                return self._table_note(ctx, f"{character.name} ไม่ใช่ผู้ใช้เวท")
            pool = list(dict.fromkeys(profile.cantrips + profile.prepared))
        reference = interpretation.spell_reference or action_text
        if not pool:
            return self._table_note(ctx, f"{character.name} ยังไม่มีคาถาที่พร้อมร่าย")
        resolution = reg.resolve_spell_name(reference, allowed_keys=pool)
        if resolution.ambiguous:
            names = ", ".join(reg.get_spell(k).name_th_hint for k in resolution.ambiguous_keys)
            return await self._enter_clarification(
                ctx, action_text, f"หมายถึงคาถาไหน: {names}?")
        if resolution.key is None:
            hint = ""
            if resolution.suggestion_keys:
                hint = " ใกล้เคียง: " + ", ".join(
                    reg.get_spell(k).name_th_hint for k in resolution.suggestion_keys)
            return self._table_note(
                ctx, f"{character.name} ไม่มีคาถาชื่อ “{reference}” ที่พร้อมร่าย.{hint}")
        spell = reg.get_spell(resolution.key)

        # 2. Resolve targets + authoritative stats (never invented by the LLM).
        npc_targets = [e for e in resolved_targets if e.entity_type != PLAYER_CHARACTER]
        pc_targets = [e for e in resolved_targets if e.entity_type == PLAYER_CHARACTER]
        needs_target = spell.attack != "none" or spell.save_ability is not None
        # An OFFENSIVE spell prefers the NPC; a spell whose declared effect lands on a
        # creature (Guidance, Bless, Shield of Faith) is cast on an ALLY, so it prefers
        # the named player character. Picking the NPC first for a buff is how "Guidance
        # on Neneko" would silently buff the nearest guard instead.
        buffs_a_creature = any(
            e.target_scope in ("self", "single") for e in spell.effects)
        if buffs_a_creature and not needs_target:
            target_ec = (pc_targets or npc_targets or [None])[0]
        else:
            target_ec = (npc_targets or pc_targets or [None])[0]
        if needs_target and target_ec is None:
            return await self._enter_clarification(
                ctx, action_text, f"จะร่าย {spell.name_th_hint} ใส่ใคร?")

        # WHO the declared effects land on. Kept separate from the combat stat
        # lookup: a buff has no AC/save numbers, and deriving its subject from those
        # is exactly why the target used to be dropped and the die never arrived.
        # `self`-scoped effects always land on the caster regardless of who was named.
        caster_ref = entity_ref("character", ctx.character_id) if ctx.character_id else None
        if any(e.target_scope == "self" for e in spell.effects):
            effect_targets = [caster_ref] if caster_ref else []
        elif target_ec is not None:
            effect_targets = [target_ec.entity_ref]
        else:
            effect_targets = [caster_ref] if caster_ref else []

        async with self.db.session() as read:
            stats = await self._target_combat_stats(read, ctx.session_id, target_ec)
        # An attack/save spell needs authoritative target numbers; without them we
        # fail safe (no slot spent) rather than invent AC/HP (spec §target).
        if needs_target and stats is None:
            return self._table_note(
                ctx, f"ยังไม่มีค่ากลไกของเป้าหมายสำหรับ {spell.name_th_hint} "
                     "(เริ่มการต่อสู้ก่อน หรือเล็งเป้าที่มีสถานะกลไก)")

        # 3. Cast + apply + commit atomically.
        target_acs = {stats["ref"]: stats["ac"]} if (stats and spell.attack != "none") else {}
        target_save_mods = ({stats["ref"]: stats["save_mods"].get(spell.save_ability, 0)}
                            if (stats and spell.save_ability) else {})
        # What the player asked the spell to create (an illusion's content/form).
        # Descriptive only — SpellEngine validates it against the spell's declared
        # limits and reports any correction in outcome.adjustments.
        effect_params = {
            "description": (interpretation.spell_description or "").strip(),
            "modes": list(interpretation.spell_modes or []),
        }
        try:
            async with self.db.unit_of_work() as s:
                caster = await s.get(Character, ctx.character_id)
                engine = SpellEngine(s, self.dice)
                scene_row = (await SceneService(s).get_active_scene(ctx.session_id)
                             if ctx.session_id else None)
                outcome = await engine.cast(
                    character=caster, spell_key=spell.name,
                    slot_level=interpretation.slot_level,
                    target_acs=target_acs, target_save_mods=target_save_mods,
                    session_id=ctx.session_id, campaign_id=ctx.campaign_id,
                    scene_id=scene_row.id if scene_row else None,
                    effect_targets=effect_targets, effect_params=effect_params,
                    location_id=scene_row.location_id if scene_row else None)
                # Apply the committed damage/healing to the authoritative target model.
                await self._apply_spell_effects(s, ctx, outcome, stats, target_ec, spell)
                # Who NOTICED a world effect, decided by the engine from perception,
                # co-location and the observer's own mind — not by the narrator.
                await self._observe_world_effects(s, ctx, outcome, scene_row)
                pm = await s.get(ProcessedMessage, ctx.processed_message_id) if ctx.processed_message_id else None
                if pm is not None:
                    pm.stage = ProcessingStage.SENT.value
                    pm.category = MessageCategory.COMMITTED_ACTION.value
                    pm.result = {"response": outcome.line_th, "spell": spell.name}
                session_row = await s.get(Session, ctx.session_id) if ctx.session_id else None
                if session_row is not None:
                    session_row.active_play_state = ActivePlayState.TABLE_OPEN.value
                    session_row.version += 1
                log.info(
                    "spell cast committed",
                    extra={"campaign_id": ctx.campaign_id, "session_id": ctx.session_id,
                           "channel_id": ctx.channel_id, "user_id": ctx.member_id,
                           "discord_message_id": ctx.inbound.discord_message_id,
                           "scene_id": scene_row.id if scene_row else None,
                           "spell": spell.name, "caster": caster_ref,
                           "effect_targets": effect_targets,
                           "effects_created": [e.effect_id for e in outcome.effects],
                           "observers": len(outcome.observations),
                           "adjustments": outcome.adjustments})
        except (RulesViolation, ValidationError) as exc:
            # Illegal cast (not known/prepared, no slot, etc.) — nothing consumed.
            log.info("spell cast rejected",
                     extra={"campaign_id": ctx.campaign_id, "spell": spell.name,
                            "reason": str(exc)})
            return self._table_note(ctx, f"ร่าย {spell.name_th_hint} ไม่ได้: {exc}")

        # 4. Presentation. MECHANICS and NARRATION are separate objects (§28), exactly
        # as the dice ritual splits them: the engine's committed roll line is shown
        # verbatim, and — because the numbers are already fixed and cannot be
        # contradicted — the narrator dramatizes the SAME committed result with the
        # bounded character context (appearance, relevant faith, active state) and the
        # canonical scene. A missed bolt, a mended wound, a blessing over a battered
        # holy symbol all read as one continuing scene, never "You cast X. +1d4."
        mech = outcome.line_th
        # A rules limit the engine applied is explained here, in the same breath as
        # the result — the player asked for something the spell cannot do and is told
        # why, rather than quietly receiving something else.
        for note in outcome.adjustments:
            mech += f"\n{note}"
        witnesses = [o["npc_name"] for o in outcome.observations if o.get("noticed")]
        if witnesses:
            mech += f"\nผู้ที่สังเกตเห็น: {', '.join(dict.fromkeys(witnesses))}"
        cast_prompt = self._cast_decision_prompt(outcome, target_ec)
        mech_outcome = ("success" if (outcome.damage or outcome.healing or outcome.effects
                                      or not needs_target) else "resolved")
        mech_msg = OutboundMessage(
            ctx.channel_id, mech, kind=MessageKind.CHECK_RESOLUTION,
            title=spell.name_th_hint,
            data={"roll_line": outcome.line_th, "outcome": mech_outcome})
        responses = [mech_msg]

        # Best-effort cinematic narration of the committed cast. It runs AFTER the
        # commit, so a narration/LLM failure can never re-roll or re-apply anything;
        # the DMNarrator's own fallback keeps a failure terse rather than raising.
        narration = await self._narrate_cast(
            ctx, action_text=action_text, spell=spell, outcome=outcome,
            resolved_targets=resolved_targets, target_ec=target_ec,
            npc_targets=npc_targets)
        if narration is not None and narration.text.strip():
            responses.append(OutboundMessage(
                ctx.channel_id, narration.text, kind=MessageKind.SCENE_FRAME,
                data={"decision_prompt": narration.decision_prompt or cast_prompt}))
        else:
            mech_msg.data["decision_prompt"] = cast_prompt
        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION, state_mutated=True,
            responses=responses,
            note=f"cast {spell.name}: dmg={outcome.damage} heal={outcome.healing} "
                 f"effects={len(outcome.effects)}")

    async def _observe_world_effects(self, s, ctx: ResolvedContext, outcome,
                                     scene_row) -> None:
        """Decide who noticed each world effect this cast created, and record it on
        the effect so later turns (and the narrator) can use it.

        The caster is excluded — they know what they made."""
        world = [e for e in outcome.effects if e.kind == "world_effect"]
        if not world or scene_row is None:
            return
        from app.models.progression import ActiveEffect as _AE
        from app.npcs.observer_service import ObserverService

        observer = ObserverService(s, self.dice)
        for granted in world:
            effect = await s.get(_AE, granted.effect_id)
            if effect is None:
                continue
            seen = await observer.observe(
                campaign_id=ctx.campaign_id, effect=effect,
                exclude_refs=[outcome.caster_ref])
            data = dict(effect.data or {})
            data["observers"] = [o.as_dict() for o in seen.noticed_by]
            effect.data = data
            outcome.observations.extend(
                {"effect_id": granted.effect_id, **o.as_dict()} for o in seen.noticed_by)

    async def _target_combat_stats(self, read, session_id: str | None, target_ec) -> dict | None:
        """Authoritative combat-relevant stats for a target, or None if unavailable.
        In combat: from the Combatant snapshot. For a player character: from the
        Character. NPCs outside combat have no stats — return None (fail safe)."""
        if target_ec is None:
            return None
        from app.tabletop.rules.derive import save_bonus
        from app.tabletop.rules.core import ABILITIES

        ref = target_ec.entity_ref
        if session_id:
            from app.models.combat import Combatant, CombatEncounter

            enc = (await read.execute(select(CombatEncounter).where(
                CombatEncounter.session_id == session_id,
                CombatEncounter.status == "active"))).scalars().first()
            if enc is not None:
                combatant = (await read.execute(select(Combatant).where(
                    Combatant.encounter_id == enc.id,
                    Combatant.entity_ref == ref))).scalars().first()
                if combatant is not None:
                    return {"ref": ref, "ac": combatant.ac, "save_mods": {},
                            "kind": "combatant", "id": combatant.id, "hp": combatant.hp}
        kind, cid = parse_entity_ref(ref)
        if kind == "character" and cid:
            char = await read.get(Character, cid)
            if char is not None:
                return {"ref": ref, "ac": char.ac,
                        "save_mods": {a: save_bonus(char, a).total for a in ABILITIES},
                        "kind": "character", "id": cid, "hp": char.hp}
        return None

    async def _apply_spell_effects(self, s, ctx, outcome, stats, target_ec, spell) -> None:
        """Apply the committed damage/healing to the authoritative target model
        (Combatant HP in combat, else Character HP). Healing defaults to the caster
        when the spell has no explicit other target."""
        if outcome.damage and stats is not None:
            if stats["kind"] == "combatant":
                from app.models.combat import Combatant

                cb = await s.get(Combatant, stats["id"])
                if cb is not None:
                    cb.hp = max(0, cb.hp - outcome.damage)
                    if cb.hp <= 0:
                        cb.alive = False
            elif stats["kind"] == "character":
                tgt = await s.get(Character, stats["id"])
                if tgt is not None:
                    tgt.hp = max(0, tgt.hp - outcome.damage)
        if outcome.healing:
            # Heal the named PC target if any, else the caster.
            heal_id = ctx.character_id
            if target_ec is not None:
                k, cid = parse_entity_ref(target_ec.entity_ref)
                if k == "character" and cid:
                    heal_id = cid
            tgt = await s.get(Character, heal_id)
            if tgt is not None:
                tgt.hp = min(tgt.max_hp, tgt.hp + outcome.healing)

    @staticmethod
    def _cast_decision_prompt(outcome, target_ec) -> str:
        if outcome.concentration:
            return "รักษาสมาธิไว้ให้ดี — จะทำอะไรต่อ?"
        return "จะทำอะไรต่อ?"

    # Spells whose class list makes them a divine act — casting one is precisely when
    # the caster's deity/oath/holy symbol becomes fictionally relevant (the flagship
    # "Bless over a broken holy symbol" case). Arcane spells never surface faith.
    _DIVINE_SPELL_CLASSES = frozenset({"cleric", "paladin"})

    async def _narrate_cast(
        self, ctx: ResolvedContext, *, action_text: str, spell, outcome,
        resolved_targets: list[EntityContext], target_ec, npc_targets,
    ):
        """Dramatize a COMMITTED cast. Read-only: builds the canonical scene + bounded
        character context (faith surfaced only for divine spells) and hands the fixed
        result to the narrator. Never rolls, never commits, and returns None on failure
        so the mechanical line still stands alone."""
        from app.memory.scene_context import SceneContextBuilder

        # Outcome word from the committed attack result (a missed bolt is a failure to
        # be narrated as one) — never re-decided here.
        if spell.attack != "none" and outcome.attack is not None:
            narr_outcome = "success" if outcome.attack.get("hit") else "failure"
        else:
            narr_outcome = "success"
        critical = bool(outcome.attack and outcome.attack.get("natural_roll") in (1, 20))
        is_divine = bool(set(spell.classes or []) & self._DIVINE_SPELL_CLASSES)
        is_save = spell.save_ability is not None
        target_ref = npc_targets[0].entity_ref if npc_targets else None
        target_name = npc_targets[0].canonical_name if npc_targets else ""
        try:
            async with self.db.session() as read:
                scene = (await SceneService(read).get_active_scene(ctx.session_id)
                         if ctx.session_id else None)
                directory = await SceneEntityDirectory(read).build(
                    scene, actor_character_id=ctx.character_id, campaign_id=ctx.campaign_id)
                character = await read.get(Character, ctx.character_id)
                scene_ctx = await SceneContextBuilder(read).build(
                    campaign_id=ctx.campaign_id, scene=scene,
                    actor_character_id=ctx.character_id)
                character_context = await build_character_narrative_context(
                    read, character=character, action_text=action_text,
                    target_name=target_name, location_name=scene_ctx.location_name,
                    threat_name=target_name, is_saving_throw=is_save,
                    is_divine_action=is_divine, campaign_id=ctx.campaign_id)
                pacing = select_pacing(
                    resolution_type=(ResolutionType.ATTACK if spell.attack != "none"
                                     else ResolutionType.SAVING_THROW if is_save
                                     else ResolutionType.ABILITY_CHECK),
                    is_saving_throw=is_save, critical=critical,
                    scene_mode=scene.mode if scene else None,
                    hook_connected=bool(character_context.relevant_hooks
                                        or character_context.faith))
                narration = await self.narrator.run(
                    read, action_text=action_text, outcome=narr_outcome,
                    result_summary=outcome.line_th, scene=scene, target_ref=target_ref,
                    directory=directory, resolved_targets=resolved_targets,
                    scene_context=scene_ctx, pacing=pacing,
                    character_context=character_context)
        except Exception as exc:  # noqa: BLE001 — narration is best-effort post-commit
            log.warning("cast narration failed; mechanical line stands alone: %s", exc)
            return None
        from app.ai.narration_guard import screen_decision_prompt, screen_narration

        actor_name = directory.actor.canonical_name if directory.actor else None
        narration.text, _ = screen_narration(narration.text, actor_name)
        narration.decision_prompt = screen_decision_prompt(narration.decision_prompt, actor_name)
        return narration

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

        # Fiction-first CHECK_SETUP: narrate up to the point uncertainty matters,
        # BEFORE any pending-action state is persisted and BEFORE the roll. No
        # outcome/DC leak — the schema/prompt structurally excludes both.
        check_setup_text = await self._build_check_setup(
            ctx, action_text=action_text, decision=decision, character=character,
            resolved_targets=resolved_targets,
        )

        pending = {
            "id": new_id(), "kind": "check",
            "member_id": ctx.member_id, "character_id": ctx.character_id,
            "action_text": action_text,
            "goal": interpretation.goal, "method": interpretation.method,
            "decision": decision.model_dump(mode="json"),
            "object_name": interpretation.object_reference,
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

        bonus_grants = await self._bonus_grants_for_check(ctx, decision, character)
        label, mod_line = self._check_label_and_mods(decision, character, bonus_grants)
        responses = []
        if check_setup_text:
            responses.append(OutboundMessage(
                ctx.channel_id, check_setup_text, kind=MessageKind.CHECK_SETUP,
            ))
        responses.append(OutboundMessage(
            ctx.channel_id, mod_line, kind=MessageKind.CHECK_PROMPT,
            title=label, choices=["🎲 ทอย d20"],
            data={"footer": "โชคชะตาอยู่ในมือเจ้า"},
        ))
        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION, state_mutated=False,
            responses=responses,
            note="pending check (dice ritual)",
        )

    async def _build_check_setup(
        self, ctx: ResolvedContext, *, action_text: str, decision, character: Character,
        resolved_targets: list[EntityContext],
    ) -> str:
        """Read-only: builds the bounded context and asks the CheckSetupGenerator
        for the pre-roll fiction beat. Never persists anything, never rolls."""
        label, _ = self._check_label_and_mods(decision, character)
        npc_targets = [e for e in resolved_targets if e.entity_type != PLAYER_CHARACTER]
        target_name = npc_targets[0].canonical_name if npc_targets else ""
        async with self.db.session() as read:
            scene = await SceneService(read).get_active_scene(ctx.session_id) if ctx.session_id else None
            directory = await SceneEntityDirectory(read).build(
                scene, actor_character_id=ctx.character_id, campaign_id=ctx.campaign_id
            )
            from app.memory.scene_context import SceneContextBuilder

            scene_ctx = await SceneContextBuilder(read).build(
                campaign_id=ctx.campaign_id, scene=scene, actor_character_id=ctx.character_id
            )
            is_saving_throw = decision.resolution_type == ResolutionType.SAVING_THROW
            character_context = await build_character_narrative_context(
                read, character=character, action_text=action_text,
                target_name=target_name, location_name=scene_ctx.location_name,
                threat_name=target_name, is_saving_throw=is_saving_throw,
                campaign_id=ctx.campaign_id,
            )
            pacing = select_pacing(
                resolution_type=decision.resolution_type,
                is_saving_throw=is_saving_throw,
                scene_mode=scene.mode if scene else None,
                hook_connected=bool(character_context.relevant_hooks),
            )
            setup = await self.check_setup.run(
                read, action_text=action_text, check_label=label, scene=scene,
                directory=directory, scene_context=scene_ctx,
                character_context=character_context, pacing=pacing,
            )
        return setup.text

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
            object_name=pending.get("object_name", ""),
        )

    def _check_label_and_mods(self, decision, character: Character,
                              bonus_grants: list | None = None) -> tuple[str, str]:
        skill = normalize_skill(decision.skill)
        ability = normalize_ability(decision.ability) or (
            ability_for_skill(skill) if skill else "wis"
        )
        modifier, proficient = check_modifier(character, ability, skill)
        name = skill or ability
        th = self._SKILL_TH.get(name, "")
        label = f"{name.replace('_', ' ').title()}{f' ({th})' if th else ''}"
        prof_note = " · ถนัด" if proficient else ""
        mod_line = f"{character.name} — โมดิฟายเออร์ {modifier:+d}{prof_note}"
        # Show the help BEFORE the roll, so the player knows the die is coming and
        # can see it was actually applied afterwards.
        for grant in bonus_grants or []:
            mod_line += f"\n{grant.label}: +{grant.expression}"
        return label, mod_line

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

    def _resolve_mechanics(self, decision, character: Character | None,
                           bonus_grants: list | None = None,
                           composed_dc=None):
        """Deterministic. Returns (check_result_or_None, outcome_str).

        Tolerant of real-LLM vocabulary: ability/skill names are normalized, and any
        unexpected value degrades to a safe WIS check rather than crashing the turn.

        `bonus_grants` are the extra dice active effects owe this roll (Guidance's
        1d4). They are rolled by the dice engine as part of the check, so the total
        the table sees already includes them.

        `composed_dc` is the situational DC (band + capped factors). Without one the
        band's bare rung is used, which is the old fixed-ladder behaviour and stays
        correct — just less responsive.
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
            dc = composed_dc.total if composed_dc is not None else resolve_dc(
                decision.dc_band)
            if rt == ResolutionType.SAVING_THROW:
                result = self.dice.resolve_saving_throw(
                    modifier=modifier, dc=dc, ability=ability,
                    advantage=decision.advantage, disadvantage=decision.disadvantage,
                    bonus_grants=bonus_grants,
                )
            else:
                result = self.dice.resolve_ability_check(
                    modifier=modifier, dc=dc, ability=ability, skill=skill, proficient=proficient,
                    advantage=decision.advantage, disadvantage=decision.disadvantage,
                    bonus_grants=bonus_grants,
                )
            return result, result.outcome
        except Exception as exc:  # noqa: BLE001 - never crash a turn on odd AI output
            log.warning("mechanics resolution fell back to a WIS check: %s", exc)
            modifier, _ = check_modifier(character, "wis", None)
            result = self.dice.resolve_ability_check(
                modifier=modifier, dc=15, ability="wis", bonus_grants=bonus_grants)
            return result, result.outcome

    # --- unresolved threads ------------------------------------------------------
    async def _has_open_question(self, ctx: ResolvedContext,
                                 npc_targets: list[EntityContext]) -> bool:
        """Is any NPC here still waiting for this character to explain something?"""
        if not ctx.character_id or not npc_targets:
            return False
        try:
            from app.npcs.memory_service import NPCMemoryService

            subject = entity_ref("character", ctx.character_id)
            async with self.db.session() as read:
                service = NPCMemoryService(read)
                for target in npc_targets:
                    kind, npc_id = parse_entity_ref(target.entity_ref)
                    if kind != "npc" or not npc_id:
                        continue
                    if await service.unresolved(npc_id=npc_id, subject_ref=subject):
                        return True
        except Exception as exc:  # noqa: BLE001
            log.warning("open-question lookup failed: %s", exc)
        return False

    async def _settle_open_questions(self, s, ctx: ResolvedContext, *,
                                     npc_targets: list[EntityContext], outcome: str,
                                     decision) -> list[str]:
        """Apply the result of an attempt to explain an open thread.

        A believed excuse CLOSES the question and eases suspicion — it never repays
        the trust the act itself cost, and the memory stays on the record. A failed
        one leaves the thread open and adds a lie to it. Either way the theft is
        still something this NPC watched happen.
        """
        if not ctx.character_id or not npc_targets:
            return []
        skill = normalize_skill(decision.skill)
        # Only an attempt to TALK your way out settles a thread; picking a lock in
        # front of the guard does not answer his question.
        if skill not in ("deception", "persuasion", "performance"):
            return []
        settled: list[str] = []
        try:
            from app.npcs.memory_service import NPCMemoryService

            service = NPCMemoryService(s)
            subject = entity_ref("character", ctx.character_id)
            believed = outcome == "success"
            for target in npc_targets:
                kind, npc_id = parse_entity_ref(target.entity_ref)
                if kind != "npc" or not npc_id:
                    continue
                for memory in await service.unresolved(npc_id=npc_id,
                                                       subject_ref=subject):
                    await service.resolve_question(
                        memory, believed=believed,
                        # Relief, not absolution: a good story takes the edge off.
                        suspicion_relief=15 if believed else 0)
                    settled.append(
                        f"{target.canonical_name}: "
                        f"{'ยอมเชื่อไปก่อน' if believed else 'ไม่เชื่อ'}")
            if settled:
                log.info("open questions settled",
                         extra={"campaign_id": ctx.campaign_id, "actor": subject,
                                "believed": believed, "settled": settled})
        except Exception as exc:  # noqa: BLE001
            log.warning("settling open questions failed: %s", exc)
        return settled

    # --- the action memory loop -------------------------------------------------
    async def _witness_action(self, s, ctx: ResolvedContext, *, decision,
                              outcome: str, scene_row, character: Character | None,
                              object_name: str = "",
                              resolved_targets: list[EntityContext] | None = None):
        """How the world perceived this action, and who was there to perceive it.

        Returns None when the action is not the kind the world remembers — which is
        most of them. Walking across a room leaves no mark; reaching into someone's
        pack does.
        """
        if character is None or not ctx.character_id:
            return None
        try:
            from app.npcs.witness_service import ActionWitnessService

            skill = normalize_skill(decision.skill)
            targets = resolved_targets or []
            return await ActionWitnessService(s).build(
                campaign_id=ctx.campaign_id, skill=skill, outcome=outcome,
                actor_ref=entity_ref("character", ctx.character_id),
                actor_name=character.name,
                # Naming the object is what turns "why were you reaching for the
                # thing?" into "why were you reaching for MY MAP?" — the difference
                # between a generic grudge and a specific one.
                object_name=object_name,
                target_name=targets[0].canonical_name if targets else "",
                location_id=scene_row.location_id if scene_row else None,
                # A SUCCESSFUL covert act is unnoticed: the check is what decided
                # whether anyone clocked it, and the engine does not re-litigate it.
                passive_noticed=False,
            )
        except Exception as exc:  # noqa: BLE001 — witnessing must not break a turn
            log.warning("witness resolution failed: %s", exc)
            return None

    async def _record_witness_memories(self, s, ctx: ResolvedContext, *, witnessed,
                                       event_id: str, scene_row,
                                       character: Character | None):
        if witnessed is None or not witnessed.memorable:
            return None
        try:
            from app.npcs.witness_service import ActionWitnessService
            from app.tabletop.effects import EffectService

            game_time = await EffectService(s).game_time(ctx.campaign_id)
            return await ActionWitnessService(s).record(
                campaign_id=ctx.campaign_id, action=witnessed, event_id=event_id,
                location_id=scene_row.location_id if scene_row else None,
                game_time=game_time, session_id=ctx.session_id,
                scene_id=scene_row.id if scene_row else None,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("witness memory recording failed: %s", exc)
            return None

    # --- situational difficulty ------------------------------------------------
    async def _compose_dc(self, ctx: ResolvedContext, decision,
                          resolved_targets: list[EntityContext] | None = None):
        """The DC this check is actually rolled against.

        The band is the task's intrinsic difficulty; the factors are what makes it
        harder or easier HERE — half read from committed state (an NPC's earned
        feeling, the weather, a fog cloud in the room), half named by the adjudicator
        from a closed vocabulary whose deltas the engine owns. The model never
        supplies a number.
        """
        from app.tabletop.adjudication import (
            SituationReader, compose_dc, factors_from_keys,
        )

        skill = normalize_skill(decision.skill)
        try:
            npc_targets = [e for e in (resolved_targets or [])
                           if e.entity_type != PLAYER_CHARACTER]
            target_ref = npc_targets[0].entity_ref if npc_targets else None
            actor_ref = (entity_ref("character", ctx.character_id)
                         if ctx.character_id else None)
            async with self.db.session() as read:
                scene = (await SceneService(read).get_active_scene(ctx.session_id)
                         if ctx.session_id else None)
                engine_factors = await SituationReader(read).factors(
                    campaign_id=ctx.campaign_id, actor_ref=actor_ref, skill=skill,
                    target_ref=target_ref, scene=scene,
                    location_id=scene.location_id if scene else None)
            proposed = factors_from_keys(
                getattr(decision, "situational_factors", None), skill=skill)
            return compose_dc(decision.dc_band, engine_factors + proposed)
        except Exception as exc:  # noqa: BLE001 — never break a turn over a DC
            log.warning("DC composition failed; using the bare band: %s", exc)
            return compose_dc(decision.dc_band, [])

    # --- active-effect integration -------------------------------------------
    def _roll_type_for(self, decision) -> str:
        from app.tabletop.effects import (
            ROLL_ABILITY_CHECK, ROLL_ATTACK, ROLL_SAVING_THROW,
        )

        rt = decision.resolution_type
        if rt == ResolutionType.SAVING_THROW:
            return ROLL_SAVING_THROW
        if rt == ResolutionType.ATTACK:
            return ROLL_ATTACK
        return ROLL_ABILITY_CHECK

    async def _bonus_grants_for_check(
        self, ctx: ResolvedContext, decision, character: Character | None,
    ) -> list:
        """The extra dice this character's active effects owe THIS check.

        Queried fresh from committed state every time — the effect layer is the
        authority on what is active, so a stale prompt can never grant a die that
        has since been consumed or expired.
        """
        if character is None or not ctx.campaign_id:
            return []
        try:
            skill = normalize_skill(decision.skill)
            ability = normalize_ability(decision.ability) or (
                ability_for_skill(skill) if skill else "wis")
            from app.tabletop.effects import EffectService

            async with self.db.session() as read:
                return await EffectService(read).bonus_grants_for(
                    campaign_id=ctx.campaign_id,
                    subject_ref=entity_ref("character", character.id),
                    roll_type=self._roll_type_for(decision), ability=ability,
                )
        except Exception as exc:  # noqa: BLE001 — a buff must never break the turn
            log.warning("bonus grants lookup failed; rolling without them: %s", exc)
            return []

    async def _consume_used_grants(self, session, check_result, grants: list) -> None:
        """End the effects that actually fed this roll. Runs inside the commit txn,
        so a die is never spent by a roll that was not committed."""
        if check_result is None or not grants:
            return
        consumable = {g.source for g in grants if g.consumed_on_use}
        used = [b.source for b in getattr(check_result, "bonus_dice", []) or []
                if b.source in consumable]
        if used:
            from app.tabletop.effects import EffectService

            await EffectService(session).consume(used, reason="spent_on_roll")

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
    def _roll_line(cls, check_result, outcome: str, composed_dc=None) -> str:
        """The player-visible mechanical line, built ONLY from committed numbers."""
        verdict = "สำเร็จ ✓" if outcome == "success" else "พลาด ✗"
        if check_result is None:
            return verdict if outcome == "success" else f"เป็นไปไม่ได้ — {verdict}"
        name = check_result.skill or check_result.ability
        th = cls._SKILL_TH.get(name, "")
        label = f"{name.capitalize()}{f' ({th})' if th else ''}"
        # Each contributing effect is named and shown with what it rolled — the die
        # is visible in the arithmetic, not silently folded into the total.
        bonus = "".join(
            f" + {b.label} {b.expression}({b.total})"
            for b in getattr(check_result, "bonus_dice", []) or []
        )
        line = (
            f"{label}: {check_result.natural_roll} + {check_result.modifier}{bonus} = "
            f"{check_result.total} vs DC {check_result.dc} — {verdict}"
        )
        # A DC that moved says why. Without this the number looks arbitrary — and a
        # world that quietly rewards a trusted friendship teaches nobody anything.
        if composed_dc is not None and composed_dc.factors:
            line += f"\n{composed_dc.explain_th()}"
        return line

    @staticmethod
    def _result_summary(check_result, outcome: str, decision) -> str:
        if check_result is None:
            return f"resolution={decision.resolution_type.value}; outcome={outcome}"
        return (
            f"{check_result.skill or check_result.ability}: "
            f"{check_result.natural_roll}+{check_result.modifier}="
            f"{check_result.total} vs DC{check_result.dc} -> {outcome}"
        )
