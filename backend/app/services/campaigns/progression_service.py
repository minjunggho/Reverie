"""ProgressionService — the engine operates the campaign as a graph.

This is the layer the audit found missing: something that knows what chapter the party
is in, what objective is active, and what happens when one resolves. Without it the
campaign was a document the narrator re-interpreted every turn
(docs/progression-audit.md, RC2).

Two rules carry most of the design:

1. **A chapter advances on RESOLUTION, not on success.** Every terminal state counts —
   including FAILED. A chapter that waits for success is one failed check away from a
   permanent deadlock, which is the "failed check blocks the only route forward"
   symptom. The story moves on changed, not stopped.

2. **Optional objectives never gate.** A missable thread cannot strand a campaign.

State transitions go through `ConsequenceService.update_quest`, so every objective
change records its Event exactly like any other world consequence — this service adds
a hierarchy, not a second write path.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_progression import Chapter, Clue
from app.models.consequences import (
    ACTIONABLE_QUEST_STATES,
    TERMINAL_QUEST_STATES,
    Quest,
)


@dataclass
class ChapterAdvance:
    """What `advance_chapter_if_resolved` actually did. Empty = nothing to do."""

    completed_chapter_key: str = ""
    opened_chapter_key: str = ""
    campaign_finished: bool = False

    @property
    def moved(self) -> bool:
        return bool(self.completed_chapter_key)


class ProgressionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- reads ---------------------------------------------------------------

    async def active_chapter(self, campaign_id: str) -> Chapter | None:
        """The chapter the party is in. At most one is ACTIVE; if authoring left none
        active, the lowest-ordered PENDING chapter is the one they are heading for."""
        active = (await self.session.execute(
            select(Chapter).where(
                Chapter.campaign_id == campaign_id, Chapter.state == "ACTIVE",
            ).order_by(Chapter.sort_order, Chapter.key)
        )).scalars().first()
        return active

    async def active_objectives(self, campaign_id: str) -> list[Quest]:
        """Objectives the party can act on now, most immediate first.

        Scoped to the active chapter plus free-floating (chapter_id IS NULL) objectives
        — a side quest stays actionable across chapters. Objectives belonging to a
        *different* chapter are deliberately excluded: offering them would point the
        party at work the campaign has not opened yet.
        """
        chapter = await self.active_chapter(campaign_id)
        rows = (await self.session.execute(
            select(Quest).where(
                Quest.campaign_id == campaign_id,
                Quest.state.in_(sorted(ACTIONABLE_QUEST_STATES)),
            ).order_by(Quest.sort_order, Quest.key)
        )).scalars().all()
        chapter_id = chapter.id if chapter is not None else None
        return [q for q in rows if q.chapter_id in (None, chapter_id)]

    async def chapter_by_key(self, campaign_id: str, key: str) -> Chapter | None:
        return (await self.session.execute(
            select(Chapter).where(
                Chapter.campaign_id == campaign_id, Chapter.key == key)
        )).scalars().first()

    # --- writes --------------------------------------------------------------

    async def start_first_chapter(self, campaign_id: str) -> Chapter | None:
        """Open the campaign's first chapter. Idempotent: if any chapter is already
        ACTIVE the campaign has started and this does nothing — re-running setup must
        never yank a party back to chapter one."""
        if await self.active_chapter(campaign_id) is not None:
            return None
        first = (await self.session.execute(
            select(Chapter).where(
                Chapter.campaign_id == campaign_id, Chapter.state == "PENDING",
            ).order_by(Chapter.sort_order, Chapter.key)
        )).scalars().first()
        if first is None:
            return None
        first.state = "ACTIVE"
        await self._open_chapter_objectives(first)
        await self.session.flush()
        return first

    async def advance_chapter_if_resolved(self, campaign_id: str) -> ChapterAdvance:
        """Complete the active chapter once every REQUIRED objective is resolved, and
        open the next one. Returns what moved so the caller can narrate it.

        Called after any objective state change. A chapter with no required objectives
        at all never auto-completes — it would otherwise complete the instant it opened.
        """
        chapter = await self.active_chapter(campaign_id)
        if chapter is None:
            return ChapterAdvance()

        required = [q for q in await self._chapter_objectives(chapter) if not q.optional]
        if not required:
            return ChapterAdvance()
        if any(q.state not in TERMINAL_QUEST_STATES for q in required):
            return ChapterAdvance()

        chapter.state = "COMPLETED"
        nxt = (await self.session.execute(
            select(Chapter).where(
                Chapter.campaign_id == campaign_id,
                Chapter.state == "PENDING",
                Chapter.sort_order > chapter.sort_order,
            ).order_by(Chapter.sort_order, Chapter.key)
        )).scalars().first()
        if nxt is None:
            await self.session.flush()
            return ChapterAdvance(completed_chapter_key=chapter.key, campaign_finished=True)
        nxt.state = "ACTIVE"
        await self._open_chapter_objectives(nxt)
        await self.session.flush()
        return ChapterAdvance(completed_chapter_key=chapter.key, opened_chapter_key=nxt.key)

    async def _chapter_objectives(self, chapter: Chapter) -> list[Quest]:
        return list((await self.session.execute(
            select(Quest).where(Quest.chapter_id == chapter.id)
            .order_by(Quest.sort_order, Quest.key)
        )).scalars().all())

    async def _open_chapter_objectives(self, chapter: Chapter) -> None:
        """An opening chapter's UNKNOWN objectives become DISCOVERED — the party now
        knows the work exists. Objectives already past UNKNOWN are left alone: the
        party may have found one early, and that discovery is not undone.

        EXCEPT clue-gated objectives. "Dive to the sunken dock" cannot be known work
        before the party learns the dock exists, so an objective some clue reveals stays
        UNKNOWN until that clue is found. The gate is DERIVED from the clue's own edge
        rather than a second flag the author has to remember to set — declaring
        `- objective: obj-dive` under a clue's `reveals` IS the statement that the
        objective is gated, and the two can never drift out of sync.
        """
        gated = await self._clue_gated_objective_keys(chapter.campaign_id)
        for q in await self._chapter_objectives(chapter):
            if q.state == "UNKNOWN" and q.key not in gated:
                q.state = "DISCOVERED"

    async def _clue_gated_objective_keys(self, campaign_id: str) -> set[str]:
        rows = (await self.session.execute(
            select(Clue).where(Clue.campaign_id == campaign_id)
        )).scalars().all()
        return {
            edge["ref"]
            for clue in rows
            for edge in (clue.reveals or [])
            if edge.get("kind") == "objective" and edge.get("ref")
        }
