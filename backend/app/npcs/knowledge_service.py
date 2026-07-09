"""NPC knowledge/belief/relationship service + the retrieval-scoped read.

`facts_npc_may_use` is the authorization boundary: it returns ONLY this NPC's own
epistemic records. It never queries objective KnowledgeRecord/Secret, so objective
truth an NPC has not learned is structurally absent from any NPC prompt.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.models.enums import KnowledgeStatus
from app.models.npc_epistemic import NPCFact, NPCRelationship


class NPCKnowledgeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_fact(
        self, *, npc_id: str, subject: str, fact: str,
        status: KnowledgeStatus = KnowledgeStatus.KNOWS, source: str = "", confidence: float = 1.0,
    ) -> NPCFact:
        row = NPCFact(
            npc_id=npc_id, subject=subject, fact=fact, status=status.value,
            source=source, confidence=confidence,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def upsert_belief(
        self, *, npc_id: str, subject: str, status: KnowledgeStatus,
        fact: str | None = None, confidence: float = 0.5, source: str = "",
    ) -> NPCFact:
        """Update the NPC's stance on a subject (or create it)."""
        existing = (
            await self.session.execute(
                select(NPCFact).where(NPCFact.npc_id == npc_id, NPCFact.subject == subject)
            )
        ).scalars().first()
        if existing is not None:
            existing.status = status.value
            existing.confidence = confidence
            if fact is not None:
                existing.fact = fact
            if source:
                existing.source = source
            return existing
        return await self.add_fact(
            npc_id=npc_id, subject=subject, fact=fact or subject, status=status,
            source=source, confidence=confidence,
        )

    async def facts_npc_may_use(self, npc_id: str) -> list[NPCFact]:
        """The retrieval boundary: this NPC's own known/believed/suspected facts only.
        FORGOTTEN and UNAWARE stances are excluded (the NPC cannot draw on them)."""
        usable = [
            KnowledgeStatus.KNOWS.value, KnowledgeStatus.BELIEVES.value,
            KnowledgeStatus.SUSPECTS.value, KnowledgeStatus.HEARD_RUMOR.value,
        ]
        rows = (
            await self.session.execute(
                select(NPCFact).where(NPCFact.npc_id == npc_id, NPCFact.status.in_(usable))
                .order_by(NPCFact.confidence.desc())
            )
        ).scalars()
        return list(rows)

    async def set_relationship(
        self, *, npc_id: str, entity_ref: str, attitude: str | None = None, trust_delta: int = 0,
    ) -> NPCRelationship:
        rel = (
            await self.session.execute(
                select(NPCRelationship).where(
                    NPCRelationship.npc_id == npc_id, NPCRelationship.entity_ref == entity_ref
                )
            )
        ).scalars().first()
        if rel is None:
            rel = NPCRelationship(npc_id=npc_id, entity_ref=entity_ref, attitude="neutral", trust=0)
            self.session.add(rel)
        if attitude is not None:
            rel.attitude = attitude
        rel.trust = (rel.trust or 0) + trust_delta
        await self.session.flush()
        return rel

    @staticmethod
    def validate_status(value: str) -> KnowledgeStatus:
        try:
            return KnowledgeStatus(value)
        except ValueError as exc:  # noqa: PERF203
            raise ValidationError(f"invalid knowledge status: {value!r}") from exc
