"""Basic social interaction (§18).

An NPC responds from ITS OWN epistemic state (retrieval-scoped). The AI PROPOSES
belief/attitude changes; the engine VALIDATES and COMMITS them as canonical records +
DM-scoped events. The NPC never speaks from objective truth it has not learned.

Whether a social *roll* is even needed is a judgement the fiction often settles
(real evidence, a sufficient bribe to a greedy NPC, a credible threat to a coward) —
this service handles the response+commit; the committed pipeline handles any roll.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.jobs import NPCResponseGenerator
from app.ai.llm.base import LLMProvider
from app.core.ids import entity_ref
from app.models.enums import EventType, Visibility
from app.npcs.knowledge_service import NPCKnowledgeService
from app.schemas.llm_io import NPCResponse
from app.services.events import EventService


@dataclass
class SocialResult:
    npc_id: str
    utterance: str
    committed_belief_changes: list[str] = field(default_factory=list)
    attitude_change: str | None = None
    # The NPC's stance toward THIS listener after recording the interaction.
    stance: str | None = None
    memory_type: str | None = None


class NPCSocialService:
    def __init__(self, db, provider: LLMProvider) -> None:
        self.db = db
        self.generator = NPCResponseGenerator(provider)

    async def respond(
        self,
        *,
        campaign_id: str,
        npc_id: str,
        listener_ref: str,
        utterance: str,
        session_id: str | None = None,
        scene_id: str | None = None,
        source_event_id: str | None = None,
    ) -> SocialResult:
        # 1. generate (read-only, epistemic-scoped context — now including this NPC's
        #    relationship with + memories of THIS specific listener).
        async with self.db.session() as read:
            from app.models.npc import NPC

            npc = await read.get(NPC, npc_id)
            listener_name = await _listener_name(read, listener_ref)
            game_time, _ = await _campaign_time_and_loc(read, campaign_id, npc_id)
            response: NPCResponse = await self.generator.run(
                read, npc=npc, listener_ref=listener_ref, utterance=utterance,
                listener_name=listener_name, game_time=game_time,
            )
        display = _compose_display(npc, response)

        # 2. validate + commit proposed deltas (engine-owned) + record the episodic
        #    memory of what this listener just did (deterministic, always for major
        #    events — the memory loop, §10).
        committed: list[str] = []
        attitude_change: str | None = None
        async with self.db.unit_of_work() as s:
            knowledge = NPCKnowledgeService(s)
            events = EventService(s)
            actor = entity_ref("npc", npc_id)

            listener_name = await _listener_name(s, listener_ref)
            game_time, location_id = await _campaign_time_and_loc(s, campaign_id, npc_id)
            from app.npcs.memory_service import NPCMemoryService

            memory = await NPCMemoryService(s).record_interaction(
                npc_id=npc_id, listener_ref=listener_ref, listener_name=listener_name,
                utterance=utterance, event_id=source_event_id,
                location_id=location_id, game_time=game_time,
            )
            recalled = await NPCMemoryService(s).recall(
                npc_id=npc_id, listener_ref=listener_ref, game_time=game_time)
            stance = recalled.relationship.current_stance if recalled.relationship else None
            memory_type = memory.memory_type

            for belief in response.proposed_belief_deltas:
                if belief.npc_id != npc_id:
                    continue  # an NPC may only update ITS OWN beliefs
                try:
                    status = NPCKnowledgeService.validate_status(belief.new_status)
                except Exception:  # noqa: BLE001 - reject invalid status, keep going
                    continue
                await knowledge.upsert_belief(
                    npc_id=npc_id, subject=belief.subject, status=status,
                    confidence=belief.confidence, source=f"social:{listener_ref}",
                )
                await events.record(
                    campaign_id=campaign_id, session_id=session_id, scene_id=scene_id,
                    event_type=EventType.NPC_STATE_CHANGED, actor_entity=actor,
                    visibility=Visibility.DM_ONLY,
                    payload={"belief": {"subject": belief.subject, "status": status.value},
                             "reason": belief.reason},
                    narrative_significance=20,
                )
                committed.append(f"{belief.subject}:{status.value}")

            if response.proposed_attitude:
                await knowledge.set_relationship(
                    npc_id=npc_id, entity_ref=listener_ref, attitude=response.proposed_attitude
                )
                await events.record(
                    campaign_id=campaign_id, session_id=session_id, scene_id=scene_id,
                    event_type=EventType.NPC_STATE_CHANGED, actor_entity=actor,
                    target_entities=[listener_ref], visibility=Visibility.DM_ONLY,
                    payload={"attitude": response.proposed_attitude}, narrative_significance=15,
                )
                attitude_change = response.proposed_attitude

        return SocialResult(
            npc_id=npc_id, utterance=display,
            committed_belief_changes=committed, attitude_change=attitude_change,
            stance=stance, memory_type=memory_type,
        )


async def _listener_name(session, listener_ref: str) -> str:
    from app.core.ids import parse_entity_ref
    from app.models.character import Character

    kind, cid = parse_entity_ref(listener_ref)
    if kind == "character" and cid:
        char = await session.get(Character, cid)
        if char is not None:
            return char.name
    return listener_ref


async def _campaign_time_and_loc(session, campaign_id: str, npc_id: str) -> tuple[int, str | None]:
    from app.models.campaign import Campaign
    from app.models.npc import NPC

    campaign = await session.get(Campaign, campaign_id)
    npc = await session.get(NPC, npc_id)
    return (campaign.current_game_time if campaign else 0,
            npc.current_location_id if npc else None)


_NONVERBAL_MODES = {"SLATE", "SIGN", "NONVERBAL", "TELEPATHY", "OTHER"}


def _compose_display(npc, response: NPCResponse) -> str:
    """The ENGINE decides final presentation from `npc.communication_mode` — never
    just trusts the model's `utterance` to remember an NPC can't speak. A non-SPOKEN
    NPC's line is always rendered as a written/nonverbal action, never as quoted
    spoken dialogue, regardless of what the model returned."""
    mode = (npc.communication_mode or "SPOKEN").upper()
    if mode not in _NONVERBAL_MODES:
        return response.spoken_text or response.utterance

    text = response.written_text or response.nonverbal_action or response.utterance
    if mode == "SLATE":
        return f"{npc.name} หยิบกระดานชนวนขึ้นมาเขียน:\n“{text}”"
    if mode == "SIGN":
        return f"{npc.name} ใช้ภาษามือสื่อสาร: {text}"
    if mode == "TELEPATHY":
        return f"เสียงของ {npc.name} ดังขึ้นในหัวของเจ้าโดยตรง:\n“{text}”"
    return f"{npc.name} {text}"
