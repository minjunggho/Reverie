"""NPCIntentionService — an NPC's plans outlive the turn that formed them.

NPCDecisionService already computed follow-ups on an escalating, engine-owned ladder
(watch → question → search → move valuables → call help → block the exit). They were
recomputed each turn from the CURRENT suspicion, rendered into one prompt, and dropped.
Two consequences, both in the brief:

- "NPC memories do not consistently create follow-up intentions" — nothing persisted,
  so nothing followed up.
- "NPC attitudes may change numerically without changing behavior" — the numbers lived
  in NPCRelationship; the behavior they implied was recomputed and lost.

And because the whole path only ran when an NPC was spoken TO, an NPC could never act
while the party was elsewhere. NPCs answered; they never created movement.

Persisting the ladder changes one thing that matters beyond bookkeeping: an intention
formed when suspicion was high SURVIVES the suspicion being talked down. A thief who
was caught and then charmed the innkeeper does not get a clean slate — the innkeeper
already decided to move the strongbox, and a pleasant conversation does not unmake a
decision. Only the intention being fulfilled, abandoned, or expired does.

Intentions are engine-derived and engine-owned throughout. The model renders one into
words; it never authors, edits, or retires one.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.npc_epistemic import NPCIntention

# The follow-up ladder's labels, mapped to stable machine kinds. `decision_service`
# owns the thresholds (how suspicious you must be before an NPC fetches help); this
# owns what the resulting plan IS.
_KIND_BY_LABEL: dict[str, str] = {
    "จับตาดูอย่างใกล้ชิด": "WATCH",
    "ซักถามให้ได้ความ": "QUESTION",
    "ขอตรวจค้นตัว": "SEARCH",
    "ย้ายของมีค่าให้พ้นมือ": "MOVE_VALUABLES",
    "เรียกพวกมาเสริม": "CALL_HELP",
    "ปิดทางออกไม่ให้ไปไหน": "BLOCK_EXIT",
}

# How urgent each plan is, and whether it can happen without the party in the room.
# MOVE_VALUABLES and CALL_HELP are the two that make an NPC an agent rather than a
# responder: an innkeeper who decided to hide the silver does it whether or not the
# party is watching, and that is what makes the world feel like it continues offscreen.
_URGENCY: dict[str, int] = {
    "WATCH": 10, "QUESTION": 25, "SEARCH": 40,
    "MOVE_VALUABLES": 55, "CALL_HELP": 70, "BLOCK_EXIT": 85,
}
_ACTS_OFFSCREEN = frozenset({"MOVE_VALUABLES", "CALL_HELP"})

# In-world minutes an NPC waits before acting on a plan it can carry out alone. Not
# instant: a shopkeeper who just decided to move the strongbox does it shortly, not
# mid-sentence.
_OFFSCREEN_DELAY_MINUTES = 30


class NPCIntentionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def pending_for(
        self, *, npc_id: str, subject_ref: str | None = None,
    ) -> list[NPCIntention]:
        """This NPC's live plans, most urgent first."""
        stmt = select(NPCIntention).where(
            NPCIntention.npc_id == npc_id, NPCIntention.state == "PENDING",
        )
        if subject_ref is not None:
            stmt = stmt.where(NPCIntention.subject_ref == subject_ref)
        rows = (await self.session.execute(stmt)).scalars().all()
        return sorted(rows, key=lambda i: i.urgency, reverse=True)

    async def sync_from_followups(
        self, *, npc_id: str, subject_ref: str, followups: list[str],
        game_time: int = 0, source_memory_id: str | None = None,
    ) -> list[NPCIntention]:
        """Persist this turn's derived follow-ups as intentions.

        Additive by design. A follow-up that is no longer derived (because suspicion
        was talked down) does NOT retire the intention it already created — see the
        module docstring. Idempotent: re-deriving the same plan next turn updates the
        existing row instead of stacking duplicates.
        """
        created: list[NPCIntention] = []
        for label in followups:
            kind = _KIND_BY_LABEL.get(label)
            if kind is None:
                continue
            existing = (await self.session.execute(
                select(NPCIntention).where(
                    NPCIntention.npc_id == npc_id,
                    NPCIntention.subject_ref == subject_ref,
                    NPCIntention.kind == kind,
                    NPCIntention.state == "PENDING",
                )
            )).scalars().first()
            if existing is not None:
                continue
            offscreen = kind in _ACTS_OFFSCREEN
            intention = NPCIntention(
                npc_id=npc_id, subject_ref=subject_ref, kind=kind, description=label,
                trigger="AFTER_TIME" if offscreen else "ON_NEXT_MEETING",
                trigger_game_time=(game_time + _OFFSCREEN_DELAY_MINUTES) if offscreen else 0,
                state="PENDING", urgency=_URGENCY.get(kind, 10),
                source_memory_id=source_memory_id,
            )
            self.session.add(intention)
            created.append(intention)
        if created:
            await self.session.flush()
        return created

    async def due(self, *, game_time: int) -> list[NPCIntention]:
        """Intentions whose time has come — the world clock's sweep. These fire whether
        or not the party is present; that is the point."""
        rows = (await self.session.execute(
            select(NPCIntention).where(
                NPCIntention.state == "PENDING",
                NPCIntention.trigger == "AFTER_TIME",
                NPCIntention.trigger_game_time <= game_time,
            )
        )).scalars().all()
        return sorted(rows, key=lambda i: i.urgency, reverse=True)

    async def fulfil(self, intention: NPCIntention) -> NPCIntention:
        intention.state = "FULFILLED"
        await self.session.flush()
        return intention

    async def abandon(self, intention: NPCIntention, *, expired: bool = False) -> NPCIntention:
        intention.state = "EXPIRED" if expired else "ABANDONED"
        await self.session.flush()
        return intention
