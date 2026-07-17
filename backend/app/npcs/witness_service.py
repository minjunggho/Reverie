"""ActionWitnessService — the loop that makes actions memorable.

The social path has always done this for TALKING: classify the utterance, commit a
typed memory, accumulate relationship dimensions. This does the same for DOING, using
the same primitives (`record_typed_memory`) and the same store
(`NPCRelationship`/`NPCMemory`) that `NPCDecisionService` already reads.

That last point is the whole fix. There is no new memory system here and no second
place to look: after this runs, `recall()` returns the theft, so the goblin's stance,
willingness and intent change — and the NPC that watched you go for its map is not a
blank slate the moment you change the subject.

Idempotent per source event: `record_typed_memory` keys on `event_id`, so a
redelivered Discord message cannot make a goblin twice as angry.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import entity_ref
from app.core.logging import get_logger
from app.models.npc import NPC
from app.npcs.action_witness import (
    DETECTION_UNNOTICED,
    SKILL_TO_ACTION,
    WitnessedAction,
    classify_action,
    detection_for,
    render_open_question,
    render_summary,
)
from app.npcs.memory_service import NPCMemoryService
from app.npcs.observer_service import ObserverService

log = get_logger(__name__)


@dataclass
class WitnessOutcome:
    """What the world took away from this action."""
    action_class: str | None
    detection: str
    memory_type: str | None = None
    witnesses: list[str] = None          # npc entity refs that recorded a memory
    open_questions: list[str] = None

    def __post_init__(self) -> None:
        self.witnesses = self.witnesses or []
        self.open_questions = self.open_questions or []

    @property
    def recorded(self) -> bool:
        return bool(self.memory_type and self.witnesses)


def action_class_for(*, skill: str | None) -> str | None:
    """The engine's own name for what the player just did.

    Derived from the SKILL the adjudicator chose, not from the words of the message:
    a Sleight of Hand check aimed at someone's belongings is a theft in Thai, in
    English, or in any phrasing a player invents.
    """
    return SKILL_TO_ACTION.get((skill or "").lower())


class ActionWitnessService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def witnesses_for(self, *, campaign_id: str, location_id: str | None,
                            exclude_refs: list[str] | None = None) -> list[NPC]:
        """NPCs positioned to perceive an action. Co-location is the presence model,
        shared with the world-effect observer rather than reinvented here."""
        candidates = await ObserverService(self.session).candidates_at(
            campaign_id=campaign_id, location_id=location_id)
        excluded = set(exclude_refs or [])
        return [n for n in candidates if entity_ref("npc", n.id) not in excluded]

    async def record(
        self, *, campaign_id: str, action: WitnessedAction, event_id: str,
        location_id: str | None = None, game_time: int = 0,
        session_id: str | None = None, scene_id: str | None = None,
    ) -> WitnessOutcome:
        """Commit one typed memory per witness, with relationship deltas.

        Returns what was recorded so the caller can surface it and so the event
        ledger can carry the witness list.
        """
        cls = classify_action(action.action_class, action.outcome, action.detection)
        if cls is None or not action.memorable:
            return WitnessOutcome(action_class=action.action_class,
                                  detection=action.detection)

        memories = NPCMemoryService(self.session)
        recorded: list[str] = []
        questions: list[str] = []
        for ref in action.witnesses:
            _, npc_id = ref.split(":", 1)
            npc = await self.session.get(NPC, npc_id)
            if npc is None:
                continue
            question = render_open_question(cls, action, npc.name)
            await memories.record_typed_memory(
                npc_id=npc_id, subject_ref=action.actor_ref,
                # One memory per (npc, event): the id is the idempotency boundary, so
                # a retried delivery updates rather than re-angers.
                event_id=f"{event_id}:{npc_id}",
                memory_type=cls.memory_type,
                summary=render_summary(cls, action, npc.name),
                importance=cls.importance, valence=cls.valence,
                source_ref=action.actor_ref, location_id=location_id,
                game_time=game_time, witnessed_directly=True,
                relationship_deltas=dict(cls.deltas),
                open_question=question,
            )
            recorded.append(ref)
            if question:
                questions.append(question)

        log.info(
            "action witnessed",
            extra={"campaign_id": campaign_id, "session_id": session_id,
                   "scene_id": scene_id, "event_id": event_id,
                   "actor": action.actor_ref, "action_class": action.action_class,
                   "outcome": action.outcome, "detection": action.detection,
                   "memory_type": cls.memory_type, "witnesses": recorded,
                   "deltas": cls.deltas})
        return WitnessOutcome(
            action_class=action.action_class, detection=action.detection,
            memory_type=cls.memory_type, witnesses=recorded,
            open_questions=questions,
        )

    async def build(
        self, *, campaign_id: str, skill: str | None, outcome: str,
        actor_ref: str, actor_name: str, location_id: str | None,
        object_name: str = "", target_name: str = "",
        passive_noticed: bool = False,
    ) -> WitnessedAction:
        """Assemble the action as the world perceived it, before recording."""
        action_class = action_class_for(skill=skill)
        detection = detection_for(outcome=outcome, action_class=action_class,
                                  passive_noticed=passive_noticed)
        witnesses: list[str] = []
        if action_class and detection != DETECTION_UNNOTICED:
            npcs = await self.witnesses_for(campaign_id=campaign_id,
                                            location_id=location_id)
            witnesses = [entity_ref("npc", n.id) for n in npcs]
        return WitnessedAction(
            action_class=action_class or "", outcome=outcome, detection=detection,
            actor_ref=actor_ref, actor_name=actor_name, object_name=object_name,
            target_name=target_name, witnesses=witnesses,
        )
