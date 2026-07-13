"""MainStoryService — the imported main story, remembered and reactive.

Persists a structured continuity record on `Campaign.main_story` so the central
storyline is never lost across turns or restarts, and keeps reacting to what
players actually do — delay, failure, alliances, deaths, unexpected choices —
WITHOUT railroading them. It records; it never forces an outcome.

Shape of `campaign.main_story`:
  {
    "dramatic_question": str,          # the central question (from central_question)
    "state": str,                      # a coarse state label the DM advances
    "hidden_truth": str,               # the concealed answer (DM-only)
    "leads": [str],                    # actionable threads still open
    "deadlines": [{"what": str, "at_minute": int}],
    "goals": [{"key","text","status"}] # status: open|completed|failed|transformed
    "branches": [{"turn","summary"}],  # player-caused divergences, in order
    "npc_states": {npc_key: str},      # important-NPC states that the story tracks
  }
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign

_VALID_GOAL_STATUS = {"open", "completed", "failed", "transformed"}


class MainStoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, campaign_id: str) -> dict:
        campaign = await self.session.get(Campaign, campaign_id)
        return dict(campaign.main_story or {}) if campaign else {}

    async def initialize_from_proposal(self, campaign_id: str, proposal) -> dict:
        """Seed the main story from an approved import: the dramatic question, the
        hidden truth (the highest-importance secret), open leads (secrets' clues +
        threats' next actions), deadlines (scheduled threats), and the main goal."""
        campaign = await self.session.get(Campaign, campaign_id)
        if campaign is None:
            return {}
        secrets = list(getattr(proposal, "secrets", []) or [])
        threats = list(getattr(proposal, "threats", []) or [])
        hidden = secrets[0].fact if secrets else ""
        leads: list[str] = []
        for s in secrets:
            leads.extend(s.clues[:1])                    # one visible lead per secret
        for t in threats:
            if (t.next_action or "").strip():
                leads.append(t.next_action)
        deadlines = [{"what": t.name, "at_minute": int(getattr(t, "scheduled_minutes", 0))}
                     for t in threats if getattr(t, "scheduled_minutes", 0)]
        goals = []
        if getattr(proposal, "central_question", ""):
            goals.append({"key": "main", "text": proposal.central_question, "status": "open"})
        story = {
            "dramatic_question": getattr(proposal, "central_question", "") or "",
            "state": "opening",
            "hidden_truth": hidden,
            "leads": leads,
            "deadlines": deadlines,
            "goals": goals,
            "branches": [],
            "npc_states": {},
        }
        campaign.main_story = story
        await self.session.flush()
        return story

    async def _mutate(self, campaign_id: str, fn) -> dict:
        import copy

        campaign = await self.session.get(Campaign, campaign_id)
        if campaign is None:
            return {}
        # Deep copy so no nested list/dict is shared with the ORM's loaded value —
        # the reassignment below is then an unambiguous change the plain JSON column
        # persists (there is no MutableDict tracking in-place edits here).
        story = copy.deepcopy(dict(campaign.main_story or {}))
        fn(story)
        campaign.main_story = story
        await self.session.flush()
        return story

    async def record_branch(self, campaign_id: str, *, turn: int, summary: str) -> dict:
        """A player choice diverged the story — record it (never discard the story)."""
        def _apply(s: dict) -> None:
            s.setdefault("branches", []).append({"turn": turn, "summary": summary})
        return await self._mutate(campaign_id, _apply)

    async def set_goal_status(self, campaign_id: str, goal_key: str, status: str) -> dict:
        if status not in _VALID_GOAL_STATUS:
            raise ValueError(f"invalid goal status {status!r}")

        def _apply(s: dict) -> None:
            for g in s.setdefault("goals", []):
                if g.get("key") == goal_key:
                    g["status"] = status
                    return
            s["goals"].append({"key": goal_key, "text": goal_key, "status": status})
        return await self._mutate(campaign_id, _apply)

    async def add_lead(self, campaign_id: str, lead: str) -> dict:
        def _apply(s: dict) -> None:
            leads = s.setdefault("leads", [])
            if lead not in leads:
                leads.append(lead)
        return await self._mutate(campaign_id, _apply)

    async def resolve_lead(self, campaign_id: str, lead: str) -> dict:
        def _apply(s: dict) -> None:
            s["leads"] = [x for x in s.get("leads", []) if x != lead]
        return await self._mutate(campaign_id, _apply)

    async def advance_state(self, campaign_id: str, state: str) -> dict:
        def _apply(s: dict) -> None:
            s["state"] = state
        return await self._mutate(campaign_id, _apply)

    async def set_npc_state(self, campaign_id: str, npc_key: str, state: str) -> dict:
        def _apply(s: dict) -> None:
            s.setdefault("npc_states", {})[npc_key] = state
        return await self._mutate(campaign_id, _apply)

    async def is_main_quest_actionable(self, campaign_id: str) -> bool:
        """The main story is actionable while the main goal is open AND at least one
        lead remains — the through-line is never a dead end."""
        story = await self.get(campaign_id)
        main_open = any(g.get("key") == "main" and g.get("status") == "open"
                        for g in story.get("goals", []))
        return main_open and bool(story.get("leads"))
