"""CampaignResetService — wipe a campaign's world/story/play in place, keep the players.

Reverie binds one campaign per Discord channel (unique `game_channel_id`) and scopes
characters to a campaign, so there was no way to start fresh without a new channel and
rebuilt characters. This resets a campaign IN PLACE: the campaign row, its members, and
their characters (identity + build + inventory) stay; everything the play produced — the
world, story, sessions, scenes, events, NPCs, progression, combat, decision windows — is
deleted, and each character is returned to a clean, rested play-state.

Destructive and irreversible: the admin command gates it behind an explicit confirmation.
"""
from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign, default_campaign_config
from app.models.campaign_progression import Chapter, Clue
from app.models.canon_import import CanonImport
from app.models.character import Character
from app.models.character_draft import CharacterDraft
from app.models.combat import Combatant, CombatEncounter
from app.models.consequences import CrimeRecord, Faction, Quest, Reputation, Rumor
from app.models.decision_window import ActionSubmission, DecisionWindow
from app.models.enums import CampaignStatus
from app.models.event import Event
from app.models.knowledge import KnowledgeRecord, Secret
from app.models.location import Location
from app.models.npc import NPC
from app.models.npc_epistemic import NPCFact, NPCIntention, NPCMemory, NPCRelationship
from app.models.processed_message import ProcessedMessage
from app.models.progression import ActiveEffect, ResourceState
from app.models.scene import Scene
from app.models.session import Session
from app.models.world import ScheduledWorldEvent, Threat
from app.models.world_graph import CampaignCanonRecord, LocationConnection

# Runtime/setup flags stripped on reset so a fresh world can be built and its opening
# plays again. Owner preferences (tone, dice_mode, planning, profile, …) are preserved.
_STRIPPED_CONFIG_KEYS = ("opening_cinematic_played", "setup_state")


class CampaignResetService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def reset(self, campaign_id: str) -> None:
        """Wipe world/story/play for the campaign; keep members + characters + build."""
        cid = campaign_id
        s = self.session

        # Subquery scopes for tables that reference a parent rather than the campaign.
        npc_ids = select(NPC.id).where(NPC.campaign_id == cid)
        session_ids = select(Session.id).where(Session.campaign_id == cid)
        encounter_ids = select(CombatEncounter.id).where(CombatEncounter.campaign_id == cid)
        window_ids = select(DecisionWindow.id).where(DecisionWindow.campaign_id == cid)
        char_ids = select(Character.id).where(Character.campaign_id == cid)

        # Delete children before parents (explicit, so we never rely on DB cascade being
        # enabled — SQLite needs a PRAGMA that isn't guaranteed on every connection).
        await s.execute(delete(NPCFact).where(NPCFact.npc_id.in_(npc_ids)))
        await s.execute(delete(NPCIntention).where(NPCIntention.npc_id.in_(npc_ids)))
        await s.execute(delete(NPCMemory).where(NPCMemory.npc_id.in_(npc_ids)))
        await s.execute(delete(NPCRelationship).where(NPCRelationship.npc_id.in_(npc_ids)))
        await s.execute(delete(Combatant).where(Combatant.encounter_id.in_(encounter_ids)))
        await s.execute(delete(ActionSubmission).where(ActionSubmission.window_id.in_(window_ids)))
        await s.execute(delete(Scene).where(Scene.session_id.in_(session_ids)))

        # Campaign-scoped world / story / play state.
        for model in (
            ActiveEffect, CombatEncounter, DecisionWindow, NPC, Clue, Chapter,
            Quest, Rumor, Reputation, CrimeRecord, Faction, ScheduledWorldEvent,
            Threat, Secret, KnowledgeRecord, CampaignCanonRecord, LocationConnection,
            Event, Session, Location, ProcessedMessage, CanonImport, CharacterDraft,
        ):
            await s.execute(delete(model).where(model.campaign_id == cid))

        # Characters STAY. Refresh expendable resources (spell slots, etc.) to full and
        # return each character to a clean, rested play-state — build/inventory untouched.
        await s.execute(
            update(ResourceState)
            .where(ResourceState.character_id.in_(char_ids))
            .values(current=ResourceState.max_value)
        )
        await s.execute(
            update(Character).where(Character.campaign_id == cid).values(
                location_id=None,
                following_character_id=None,
                hp=Character.max_hp,
                temp_hp=0,
                conditions=[],
                exhaustion=0,
                dead=False,
                stable=False,
                death_saves={"successes": 0, "failures": 0},
            )
        )

        # Return the campaign to a clean SETUP state so a new world can be created/imported
        # and its opening plays again. Owner-tunable config is preserved.
        campaign = await s.get(Campaign, cid)
        if campaign is not None:
            config = {k: v for k, v in (campaign.config or default_campaign_config()).items()
                      if k not in _STRIPPED_CONFIG_KEYS}
            campaign.config = config
            campaign.status = CampaignStatus.SETUP.value
            campaign.current_game_time = 0
            campaign.event_seq = 0
            campaign.brief = ""
            campaign.central_question = ""
            campaign.session_prep = {}
            campaign.main_story = {}
            campaign.default_session_opening = ""
            campaign.starting_location_id = None
            campaign.current_party_anchor_id = None
