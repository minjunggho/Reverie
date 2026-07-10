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
from app.core.ids import SYSTEM_ACTOR, entity_ref
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
from app.orchestration.commitment import CommittedAction
from app.orchestration.context import ResolvedContext
from app.services.events import EventService
from app.services.scenes import SceneService
from app.services.sessions import SessionService
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
        self.rng = rng
        self.dice = DiceEngine(rng)
        self.interpreter = ActionInterpreter(provider)
        self.adjudicator = AdjudicationJudge(provider)
        self.consequence = ConsequencePlanner(provider)
        self.narrator = DMNarrator(provider)

    # --- entry points --------------------------------------------------------
    async def handle(self, ctx: ResolvedContext, action: CommittedAction) -> BridgeResult:
        return await self._process(ctx, action.action_text, allow_clarify=True)

    async def resume_clarification(
        self, ctx: ResolvedContext, *, answer_text: str, pending: dict
    ) -> BridgeResult:
        original = pending.get("action_text", "")
        merged = f"{original} ({answer_text})".strip()
        async with self.db.unit_of_work() as s:
            scene = await SceneService(s).get_active_scene(ctx.session_id)
            if scene is not None:
                await SceneService(s).clear_pending_action(scene)
            session_row = await s.get(Session, ctx.session_id)
            if session_row is not None:
                session_row.active_play_state = ActivePlayState.TABLE_OPEN.value
                session_row.version += 1
        # Do not clarify twice — proceed with the merged intent (assume-and-state).
        return await self._process(ctx, merged, allow_clarify=False)

    # --- core ----------------------------------------------------------------
    async def _process(self, ctx: ResolvedContext, action_text: str, *, allow_clarify: bool) -> BridgeResult:
        # Steps 1-9: load context, interpret, adjudicate (all read-only).
        async with self.db.session() as read:
            scene = await SceneService(read).get_active_scene(ctx.session_id) if ctx.session_id else None
            character = await read.get(Character, ctx.character_id) if ctx.character_id else None
            interpretation = await self.interpreter.run(
                read, action_text=action_text, scene=scene, character=character
            )
            decision = await self.adjudicator.run(
                read, action_text=action_text, interpretation=interpretation,
                scene=scene, character=character,
            )
            target_ref = self._primary_npc_target(scene)

        # Step 10: clarification.
        if allow_clarify:
            clarify = decide_clarification(interpretation, decision)
            if clarify.needs_clarification:
                question = clarify.question or self._phrase_missing(interpretation)
                return await self._enter_clarification(ctx, action_text, question)

        # Steps 11-12: adjudicate resolution + deterministic dice.
        check_result, outcome = self._resolve_mechanics(decision, character)

        # Step 13: consequence proposal.
        async with self.db.session() as read:
            scene2 = await SceneService(read).get_active_scene(ctx.session_id) if ctx.session_id else None
            consequence = await self.consequence.run(
                read, action_text=action_text, outcome=outcome, scene=scene2, target_ref=target_ref
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
                payload={"action_text": action_text, "goal": interpretation.goal,
                         "method": interpretation.method,
                         "summary": f"{interpretation.goal}"},
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
            _, rejected = await applier.apply_valid(consequence.deltas)

            pm = await s.get(ProcessedMessage, ctx.processed_message_id) if ctx.processed_message_id else None
            if pm is not None:
                pm.stage = ProcessingStage.COMMITTED.value
                pm.category = MessageCategory.COMMITTED_ACTION.value

            session_row = await s.get(Session, ctx.session_id) if ctx.session_id else None
            if session_row is not None:
                session_row.active_play_state = ActivePlayState.TABLE_OPEN.value
                session_row.version += 1

        if rejected:
            log.warning("dropped %d illegal consequence delta(s): %s",
                        len(rejected), [r[1] for r in rejected])

        # Step 17: narration built from the committed result.
        result_summary = self._result_summary(check_result, outcome, decision)
        async with self.db.session() as read:
            scene3 = await SceneService(read).get_active_scene(ctx.session_id) if ctx.session_id else None
            narration = await self.narrator.run(
                read, action_text=action_text, outcome=outcome,
                result_summary=result_summary, scene=scene3, target_ref=target_ref,
            )

        # Step 18-19: cache response + mark SENT.
        async with self.db.unit_of_work() as s:
            pm = await s.get(ProcessedMessage, ctx.processed_message_id) if ctx.processed_message_id else None
            if pm is not None:
                pm.stage = ProcessingStage.SENT.value
                pm.result = {"response": narration.text, "outcome": outcome}

        return BridgeResult(
            handled=True, category=MessageCategory.COMMITTED_ACTION, state_mutated=True,
            responses=[OutboundMessage(ctx.channel_id, narration.text)],
            note=f"outcome={outcome}; consequence={consequence.consequence_class.value}",
        )

    # --- helpers -------------------------------------------------------------
    async def _enter_clarification(
        self, ctx: ResolvedContext, action_text: str, question: str
    ) -> BridgeResult:
        from app.core.ids import new_id

        pending = {
            "id": new_id(),
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

    @staticmethod
    def _primary_npc_target(scene) -> str | None:
        if scene is None:
            return None
        for ref in list(scene.immediate_threat_ids or []) + list(scene.visible_entity_ids or []):
            if ref.startswith("npc:"):
                return ref
        return None

    @staticmethod
    def _phrase_missing(interpretation) -> str:
        if interpretation.missing_information:
            return interpretation.missing_information[0]
        return "ช่วยบอกให้ชัดขึ้นอีกนิดได้ไหม"

    @staticmethod
    def _result_summary(check_result, outcome: str, decision) -> str:
        if check_result is None:
            return f"resolution={decision.resolution_type.value}; outcome={outcome}"
        return (
            f"{check_result.skill or check_result.ability}: "
            f"{check_result.natural_roll}+{check_result.modifier}="
            f"{check_result.total} vs DC{check_result.dc} -> {outcome}"
        )
