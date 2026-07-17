"""ProgressionContext — the campaign's direction, present on EVERY turn.

The narrator used to receive the campaign goal exactly once, in the Session 1
prologue, and never again (see docs/progression-audit.md, RC1). From turn 2 onward it
was handed a location, a cast, and the last action — so it reacted to the last message,
because that was all it had. This builder is the fix: it assembles the authoritative
answer to "what is this campaign about, and what can the party do next" from persisted
state, on every turn.

Authority runs one way. This block is engine-owned truth the narrator must PRESERVE and
may not contradict, extend, or invent leads into. The narrator renders direction; it
never authors it.

SAFETY: `main_story` also holds `hidden_truth` — the concealed answer to the dramatic
question. This context is assembled for PLAYER-FACING narration, so `hidden_truth` is
never read here. The DM-only planning path (pressure_block) is where concealed material
belongs. Do not add it to this dataclass.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign

# The brief's pacing rule: at most times the party should see roughly two to four
# meaningful opportunities. Fewer reads as a railroad; more reads as noise and lets the
# narrator cherry-pick a lead the party has no route to.
MAX_VISIBLE_LEADS = 4


@dataclass
class ProgressionContext:
    """What the engine knows about where the campaign is going. Player-safe."""

    campaign_goal: str = ""
    chapter_goal: str = ""
    active_objective: str = ""
    leads: list[str] = field(default_factory=list)

    @property
    def has_direction(self) -> bool:
        return bool(self.campaign_goal or self.active_objective or self.leads)

    def as_block(self) -> str:
        """Render the direction the narrator must keep in view.

        Empty string when the campaign has no direction at all (an un-imported or
        hand-made campaign) — an empty header would just be prompt noise.
        """
        if not self.has_direction:
            return ""
        lines = ["CAMPAIGN_DIRECTION (ทิศทางที่ engine กำหนด — ห้ามขัด ห้ามแต่งเพิ่ม):"]
        if self.campaign_goal:
            lines.append(f"- GOAL: {self.campaign_goal}")
        if self.chapter_goal:
            lines.append(f"- CHAPTER: {self.chapter_goal}")
        if self.active_objective:
            lines.append(f"- OBJECTIVE: {self.active_objective}")
        if self.leads:
            lines.append("- OPEN_LEADS:")
            lines.extend(f"  - {lead}" for lead in self.leads)
        return "\n".join(lines)


class ProgressionContextBuilder:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build(self, *, campaign_id: str) -> ProgressionContext:
        campaign = await self.session.get(Campaign, campaign_id)
        if campaign is None:
            return ProgressionContext()
        story = dict(campaign.main_story or {})

        main = next((g for g in story.get("goals", []) if g.get("key") == "main"), None)
        if main is not None:
            # An explicit main goal governs, and it is direction only while OPEN. Once
            # it is completed/failed/transformed the campaign has moved past it, and
            # presenting it would steer the party at something already resolved.
            goal = main.get("text", "") if main.get("status") == "open" else ""
        else:
            # No main goal recorded — a hand-made campaign, or one imported before
            # main_story existed. The central question is the only direction there is.
            goal = campaign.central_question or ""

        # The objective layer. A campaign with no chapters (hand-made, or imported
        # before chapters existed) simply contributes nothing here — goal + leads
        # still reach the narrator.
        from app.services.campaigns.progression_service import ProgressionService

        progression = ProgressionService(self.session)
        chapter = await progression.active_chapter(campaign_id)
        objectives = await progression.active_objectives(campaign_id)
        # The immediate task is the first actionable objective; the rest are context
        # the party can also act on and surface as leads below.
        immediate = objectives[0] if objectives else None

        return ProgressionContext(
            campaign_goal=goal,
            # hidden_purpose is DM-only and deliberately not read (see module docstring).
            chapter_goal=chapter.goal if chapter is not None else "",
            active_objective=(immediate.task or immediate.name) if immediate is not None else "",
            leads=self._leads(story, objectives[1:]),
        )

    @staticmethod
    def _leads(story: dict, other_objectives: list) -> list[str]:
        """Open objectives are stronger leads than main_story's free-text threads: they
        are typed, stateful, and the engine knows when they resolve. They go first, and
        story leads fill whatever room is left up to the visible cap."""
        leads = [(q.task or q.name) for q in other_objectives if (q.task or q.name)]
        for text in story.get("leads", []):
            if len(leads) >= MAX_VISIBLE_LEADS:
                break
            if text and text not in leads:
                leads.append(text)
        return leads[:MAX_VISIBLE_LEADS]
