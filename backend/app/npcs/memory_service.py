"""NPCMemoryService — the episodic-memory + relationship-dimension loop (§7, §10).

Turns a committed player→NPC interaction into: (1) a typed episodic `NPCMemory`
about that specific character, linked to the source event; (2) accumulated changes
to the multi-dimensional `NPCRelationship` the NPC holds toward that character.
Retrieval hands both back — scoped to ONE npc and ONE listener — so an NPC treats
each party member differently and remembers what they did across sessions.

Interaction classification is deterministic (keyword-based) so that MAJOR events
(threats, violence, rescue) ALWAYS create a memory — never dependent on a model
remembering to. An LLM-proposed refinement can layer on top later.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.npc_epistemic import RELATIONSHIP_DIMENSIONS, NPCMemory, NPCRelationship

_CLAMP_LO, _CLAMP_HI = -100, 100


@dataclass
class InteractionClass:
    memory_type: str
    importance: int
    valence: int
    deltas: dict[str, int]
    stance: str
    summary_prefix: str


# Deterministic Thai/English keyword → interaction classification. Order matters:
# the most consequential match wins. Every classification also nudges familiarity.
_RULES: list[tuple[tuple[str, ...], InteractionClass]] = [
    (("ฆ่า", "ทำร้าย", "แทง", "ฟัน", "ต่อย", "assault", "attack", "kill"),
     InteractionClass("ASSAULT", 85, -3,
                      {"fear": 35, "anger": 25, "trust": -30, "suspicion": 25}, "hostile",
                      "ถูกทำร้ายโดย")),
    (("ขู่", "เตือนครั้งสุดท้าย", "ถ้าไม่", "จะเสียใจ", "ระวังตัว", "threaten", "or else"),
     InteractionClass("THREAT", 70, -3,
                      {"fear": 25, "anger": 15, "trust": -20, "suspicion": 20}, "afraid",
                      "ถูกข่มขู่โดย")),
    (("ช่วยชีวิต", "พาหนี", "ปกป้อง", "rescue", "saved"),
     InteractionClass("RESCUE", 90, 3,
                      {"trust": 30, "obligation": 40, "respect": 20, "affection": 15}, "loyal",
                      "ได้รับการช่วยชีวิตจาก")),
    (("ช่วย", "รักษา", "help", "heal"),
     InteractionClass("HELP", 60, 2,
                      {"trust": 20, "obligation": 25, "respect": 10, "affection": 10}, "grateful",
                      "ได้รับความช่วยเหลือจาก")),
    (("ขอบคุณ", "ขอบใจ", "ซึ้งใจ", "thank"),
     InteractionClass("AFFECTION", 25, 2,
                      {"affection": 12, "familiarity": 8}, "warm",
                      "ได้รับคำขอบคุณจาก")),
    (("มอบ", "ของขวัญ", "ให้เหรียญ", "ให้ทอง", "gift", "ให้ของ"),
     InteractionClass("GIFT", 40, 2,
                      {"affection": 15, "trust": 10, "obligation": 10}, "warm",
                      "ได้รับของจาก")),
    (("โกหก", "หลอก", "lie", "deceiv"),
     InteractionClass("LIE", 55, -2,
                      {"trust": -25, "suspicion": 25, "anger": 10}, "wary",
                      "ถูกโกหกโดย")),
    (("ด่า", "สารเลว", "โง่", "ไอ้", "insult", "หยาบคาย"),
     InteractionClass("INSULT", 45, -2,
                      {"anger": 20, "respect": -15, "trust": -10}, "offended",
                      "ถูกดูหมิ่นโดย")),
    (("สัญญา", "รับปาก", "จะกลับมา", "promise"),
     InteractionClass("PROMISE", 50, 1,
                      {"trust": 5, "obligation": 5}, "hopeful",
                      "ได้รับสัญญาจาก")),
]

_DEFAULT = InteractionClass("INTERACTION", 12, 0, {}, "neutral", "คุยกับ")


def classify_interaction(utterance: str) -> InteractionClass:
    low = (utterance or "").lower()
    for keys, cls in _RULES:
        if any(k in low for k in keys):
            return cls
    return _DEFAULT


@dataclass
class RecalledContext:
    relationship: NPCRelationship | None
    memories: list[NPCMemory] = field(default_factory=list)

    def as_prompt_block(self, listener_name: str) -> str:
        """Compact, retrieval-scoped block for the NPC prompt: how this NPC feels
        about THIS listener + the specific things they remember them doing."""
        lines: list[str] = []
        rel = self.relationship
        if rel is not None:
            dims = [f"{d}={getattr(rel, d)}" for d in RELATIONSHIP_DIMENSIONS
                    if getattr(rel, d, 0)]
            stance = rel.current_stance or rel.attitude or "neutral"
            lines.append(f"ความรู้สึกต่อ {listener_name}: stance={stance}"
                         + (f"; {', '.join(dims)}" if dims else "; (ยังไม่คุ้นเคย)"))
        if self.memories:
            lines.append(f"สิ่งที่จำได้เกี่ยวกับ {listener_name} (ล่าสุด/สำคัญสุดก่อน):")
            for m in self.memories:
                lines.append(f"- [{m.memory_type}] {m.summary}")
        return "\n".join(lines)


class NPCMemoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _relationship(self, npc_id: str, entity_ref: str) -> NPCRelationship:
        rel = (await self.session.execute(
            select(NPCRelationship).where(NPCRelationship.npc_id == npc_id,
                                          NPCRelationship.entity_ref == entity_ref)
        )).scalars().first()
        if rel is None:
            rel = NPCRelationship(npc_id=npc_id, entity_ref=entity_ref)
            self.session.add(rel)
            await self.session.flush()
        return rel

    async def record_interaction(
        self, *, npc_id: str, listener_ref: str, listener_name: str, utterance: str,
        event_id: str | None = None, location_id: str | None = None,
        game_time: int = 0, witnessed_directly: bool = True,
    ) -> NPCMemory:
        """Commit one episodic memory about `listener_ref` and accumulate the
        relationship deltas. Idempotent per source event: re-recording the same
        event_id updates in place rather than duplicating (Discord retries)."""
        cls = classify_interaction(utterance)

        existing = None
        if event_id is not None:
            existing = (await self.session.execute(
                select(NPCMemory).where(NPCMemory.npc_id == npc_id,
                                        NPCMemory.event_id == event_id)
            )).scalars().first()
        summary = f"{listener_name} — {cls.summary_prefix} {listener_name}: “{utterance.strip()[:120]}”"
        if existing is not None:
            existing.memory_type = cls.memory_type
            existing.summary = summary
            existing.importance = cls.importance
            existing.emotional_valence = cls.valence
            memory = existing
        else:
            memory = NPCMemory(
                npc_id=npc_id, subject_ref=listener_ref, event_id=event_id,
                memory_type=cls.memory_type, summary=summary,
                importance=cls.importance, emotional_valence=cls.valence,
                witnessed_directly=witnessed_directly, source_ref=listener_ref,
                location_id=location_id, game_time=game_time,
            )
            self.session.add(memory)

        # Accumulate relationship dimensions only for a new source event. Discord
        # retries may refresh the memory text, but must never apply trust twice.
        if existing is not None:
            await self.session.flush()
            return memory

        # Every new interaction also
        # increases familiarity a little — the NPC now knows this person better.
        rel = await self._relationship(npc_id, listener_ref)
        deltas = dict(cls.deltas)
        deltas["familiarity"] = deltas.get("familiarity", 0) + 3
        for dim, delta in deltas.items():
            if dim in RELATIONSHIP_DIMENSIONS:
                cur = int(getattr(rel, dim) or 0)
                setattr(rel, dim, max(_CLAMP_LO, min(_CLAMP_HI, cur + delta)))
        rel.current_stance = _derive_stance(rel)
        # Keep the coarse back-compat fields aligned.
        rel.trust = int(getattr(rel, "trust", 0))
        rel.attitude = rel.current_stance
        if event_id is not None:
            rel.last_interaction_event_id = event_id
        await self.session.flush()
        return memory

    async def record_typed_memory(
        self, *, npc_id: str, subject_ref: str, event_id: str,
        memory_type: str, summary: str, importance: int, valence: int,
        source_ref: str, location_id: str | None = None, game_time: int = 0,
        witnessed_directly: bool = True,
        relationship_deltas: dict[str, int] | None = None,
        open_question: str = "",
    ) -> NPCMemory:
        """Commit a validated domain memory through the existing memory system.

        ``event_id`` is mandatory and is the idempotency boundary. Callers own the
        domain vocabulary; this method owns relationship accumulation and clamps.

        ``open_question`` marks the memory as an unresolved thread the NPC is still
        carrying — the mechanism that stops a change of subject from retiring it.
        """
        existing = (await self.session.execute(select(NPCMemory).where(
            NPCMemory.npc_id == npc_id, NPCMemory.event_id == event_id
        ))).scalars().first()
        if existing is not None:
            return existing
        memory = NPCMemory(
            npc_id=npc_id, subject_ref=subject_ref, event_id=event_id,
            memory_type=memory_type, summary=summary, importance=max(0, min(100, importance)),
            emotional_valence=max(-3, min(3, valence)),
            witnessed_directly=witnessed_directly, source_ref=source_ref,
            location_id=location_id, game_time=game_time,
            open_question=open_question, resolved=False,
        )
        self.session.add(memory)
        rel = await self._relationship(npc_id, subject_ref)
        for dim, delta in (relationship_deltas or {}).items():
            if dim in RELATIONSHIP_DIMENSIONS:
                current = int(getattr(rel, dim) or 0)
                setattr(rel, dim, max(_CLAMP_LO, min(_CLAMP_HI, current + int(delta))))
        rel.current_stance = _derive_stance(rel)
        rel.trust = int(getattr(rel, "trust", 0))
        rel.attitude = rel.current_stance
        rel.last_interaction_event_id = event_id
        await self.session.flush()
        return memory

    async def unresolved(
        self, *, npc_id: str, subject_ref: str, limit: int = 3
    ) -> list[NPCMemory]:
        """Threads this NPC is still pulling on about this character.

        An unanswered question outlives a change of subject — which is exactly what
        was missing: a player could talk about anything else and the NPC would move
        on as though nothing had happened.
        """
        return list((await self.session.execute(
            select(NPCMemory).where(
                NPCMemory.npc_id == npc_id, NPCMemory.subject_ref == subject_ref,
                NPCMemory.active.is_(True), NPCMemory.resolved.is_(False),
                NPCMemory.open_question != "",
            ).order_by(NPCMemory.importance.desc(), NPCMemory.game_time.desc())
            .limit(limit)
        )).scalars())

    async def resolve_question(
        self, memory: NPCMemory, *, believed: bool, suspicion_relief: int = 0,
    ) -> NPCRelationship:
        """Close (or harden) an open thread after the character addressed it.

        A BELIEVED explanation stops the NPC asking, and eases suspicion — but never
        clears it, and never repays the trust the act itself cost. "I believe you"
        is not "I forgot". The memory stays on the record, still recallable, still
        colouring every future interaction.

        A DISBELIEVED explanation leaves the thread open AND adds a lie to the
        record: trying to talk your way out and failing is worse than saying nothing.
        """
        rel = await self._relationship(memory.npc_id, memory.subject_ref)
        if believed:
            memory.resolved = True
            if suspicion_relief:
                rel.suspicion = max(
                    _CLAMP_LO, min(_CLAMP_HI,
                                   int(rel.suspicion or 0) - abs(suspicion_relief)))
        else:
            # The question stays open; the failed excuse is its own wound.
            for dim, delta in (("suspicion", 20), ("trust", -15), ("anger", 10)):
                current = int(getattr(rel, dim) or 0)
                setattr(rel, dim, max(_CLAMP_LO, min(_CLAMP_HI, current + delta)))
        rel.current_stance = _derive_stance(rel)
        rel.attitude = rel.current_stance
        await self.session.flush()
        return rel

    async def recall(
        self, *, npc_id: str, listener_ref: str, limit: int = 5, game_time: int = 0
    ) -> RecalledContext:
        """Retrieve the NPC's feeling about + strongest active memories of ONE
        listener. Ordered by importance then recency (an old assault outranks a
        recent greeting). Bumps last_recalled_at on what was surfaced."""
        rel = (await self.session.execute(
            select(NPCRelationship).where(NPCRelationship.npc_id == npc_id,
                                          NPCRelationship.entity_ref == listener_ref)
        )).scalars().first()
        memories = list((await self.session.execute(
            select(NPCMemory).where(
                NPCMemory.npc_id == npc_id, NPCMemory.subject_ref == listener_ref,
                NPCMemory.active.is_(True))
            .order_by(NPCMemory.importance.desc(), NPCMemory.game_time.desc(),
                      NPCMemory.created_at.desc())
            .limit(limit)
        )).scalars())
        for m in memories:
            m.last_recalled_at = game_time
        return RecalledContext(relationship=rel, memories=memories)


def _derive_stance(rel: NPCRelationship) -> str:
    """A single readable stance from the dimensions — the dominant feeling. A single
    consequential interaction is enough to shift the stance off neutral; repeated
    ones deepen it (fear/anger climb toward the harsher labels)."""
    if rel.anger >= 40:
        return "hostile"
    if rel.fear >= 20:
        return "afraid"
    if rel.anger >= 20:
        return "hostile"
    if rel.suspicion >= 20 and rel.trust <= 0:
        return "wary"
    if rel.obligation >= 30 or (rel.trust >= 25 and rel.affection >= 15):
        return "loyal"
    if rel.trust >= 15 or rel.affection >= 15:
        return "friendly"
    if rel.anger >= 10 or rel.suspicion >= 15:
        return "guarded"
    return "neutral"
