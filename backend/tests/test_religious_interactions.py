"""Phase 3 faith interactions: epistemic context, memories and temple policy."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError, ValidationError
from app.models.character import Character
from app.models.npc import NPC
from app.models.npc_epistemic import NPCMemory, NPCRelationship
from app.models.world import Threat
from app.models.world_graph import CampaignCanonRecord
from app.memory.context_builders import build_npc_response_context
from app.npcs import NPCService, NPCSocialService
from app.schemas.belief import (
    BeliefProfile, BeliefSource, BeliefStance, BeliefVisibility, DevotionLevel,
    ReligiousKnowledgeLevel, ReligiousRole,
)
from app.schemas.religious_interaction import (
    ReligiousKnowledgeSource, TempleArea, TemplePolicy, TempleServiceKind,
    TempleServicePolicy,
)
from app.services.beliefs import BeliefService
from app.services.economy.wallet_service import WalletService
from app.services.faith import FaithService
from app.services.religious_interactions import ReligiousBehavior, ReligiousInteractionService
from tests.support.factories import build_world


def _belief(
    deity: str, *, role: ReligiousRole | None = None,
    visibility: BeliefVisibility = BeliefVisibility.PUBLIC,
    interpretation: str | None = None,
) -> BeliefProfile:
    return BeliefProfile(
        primary_deity_key=deity,
        stance=(BeliefStance.SECRET_BELIEVER
                if visibility is BeliefVisibility.SECRET else BeliefStance.BELIEVER),
        devotion=DevotionLevel.COMMITTED,
        visibility=visibility,
        religious_role=role,
        knowledge_level=(ReligiousKnowledgeLevel.DEEP if role is ReligiousRole.PRIEST
                         else ReligiousKnowledgeLevel.CULTURAL),
        personal_interpretation=interpretation,
        sacred_symbol=f"symbol of {deity}", source=BeliefSource.PLAYER_AUTHORED,
        provenance="TEST",
    )


async def _faith_people(db, *, player_deity="selune", npc_deity="selune",
                        secret=False, interpretation=None):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        await FaithService(s).activate_pantheon(world.campaign_id, "forgotten_realms")
        char = await s.get(Character, world.kael_id)
        npc = await s.get(NPC, world.guard_npc_id)
        await BeliefService(s).set_character_belief(
            char, _belief(player_deity, visibility=(BeliefVisibility.SECRET if secret else BeliefVisibility.PUBLIC)),
            cleric_deity_key=None, cleric_domain=None,
        )
        await BeliefService(s).set_npc_belief(
            npc, _belief(npc_deity, role=ReligiousRole.PRIEST, interpretation=interpretation)
        )
    return world


async def test_same_faith_priest_recognizes_visible_symbol_without_automatic_trust(db):
    world = await _faith_people(db)
    async with db.session() as s:
        service = ReligiousInteractionService(s)
        context = await service.build_context(
            campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
            character_id=world.kael_id,
        )
        outcomes = await service.evaluate_special_interactions(context)
        assert context.shared_deity is True
        assert context.visible_symbols == ("symbol of selune",)
        assert context.npc_religious_role == "PRIEST"
        assert any(item.kind.value == "RECOGNITION" for item in outcomes)
        assert all(item.payload.get("trust_delta", 0) == 0 for item in outcomes)
        assert context.relationship_state is None
        npc = await s.get(NPC, world.guard_npc_id)
        prompt = await build_npc_response_context(
            s, npc=npc, listener_ref=f"character:{world.kael_id}",
            listener_name="Kael", utterance="Hello",
        )
        blob = "\n".join(item["content"] for item in prompt)
        assert "RELIGIOUS_CONTEXT" in blob
        assert "automatic trust/help is forbidden" in blob
        assert "full_owner_provided_lore" not in blob


async def test_hidden_belief_is_per_npc_and_revelation_is_remembered(db):
    world = await _faith_people(db, secret=True)
    async with db.unit_of_work() as s:
        other = await NPCService(s).create_npc(
            campaign_id=world.campaign_id, name="Other priest", personality="quiet",
            current_location_id=world.location_id,
        )
        await BeliefService(s).set_npc_belief(
            other, _belief("selune", role=ReligiousRole.PRIEST)
        )
        other_id = other.id
    async with db.session() as s:
        hidden = await ReligiousInteractionService(s).build_context(
            campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
            character_id=world.kael_id,
        )
        assert hidden.player_public_belief is None
        assert hidden.player_known_private_belief is None
    async with db.unit_of_work() as s:
        await ReligiousInteractionService(s).reveal_belief(
            campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
            character_id=world.kael_id, source_event_id="reveal-1",
        )
    async with db.session() as s:
        service = ReligiousInteractionService(s)
        known = await service.build_context(
            campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
            character_id=world.kael_id,
        )
        unknown = await service.build_context(
            campaign_id=world.campaign_id, npc_id=other_id,
            character_id=world.kael_id,
        )
        assert known.player_known_private_belief["primary_deity_key"] == "selune"
        assert "revealed" in known.important_religious_memories[0]
        assert unknown.player_known_private_belief is None


async def test_explicit_disclosure_flows_through_real_social_service(db, provider):
    world = await _faith_people(db, secret=True)
    result = await NPCSocialService(db, provider).respond(
        campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
        listener_ref=f"character:{world.kael_id}",
        utterance="I follow Selûne, though I keep that faith hidden.",
        source_event_id="social-disclosure-1",
    )
    assert result.religious_disclosure is True
    async with db.session() as s:
        context = await ReligiousInteractionService(s).build_context(
            campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
            character_id=world.kael_id,
        )
        assert context.player_known_private_belief["primary_deity_key"] == "selune"
        assert any("revealed" in item for item in context.important_religious_memories)


async def test_rival_faith_is_tension_not_automatic_combat(db):
    world = await _faith_people(db, player_deity="shar", npc_deity="selune")
    async with db.session() as s:
        service = ReligiousInteractionService(s)
        context = await service.build_context(
            campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
            character_id=world.kael_id,
        )
        outcomes = await service.evaluate_special_interactions(context)
        warning = next(item for item in outcomes if item.kind.value == "WARNING")
        assert context.rival_deities == ("shar",)
        assert warning.payload["combat"] is False


async def test_priest_gets_bounded_doctrine_and_interpretation_can_disagree(db):
    world = await _faith_people(
        db, player_deity="tyr", npc_deity="tyr", interpretation="Law before mercy"
    )
    async with db.session() as s:
        service = ReligiousInteractionService(s)
        context = await service.build_context(
            campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
            character_id=world.kael_id,
        )
        assert context.npc_religious_knowledge == "DEEP"
        assert context.doctrine[0].values == ("justice", "law", "honest judgment")
        assert context.npc_belief["personal_interpretation"] == "Law before mercy"
        assert "full_owner_provided_lore" not in context.as_prompt_block()
        # Give the player a distinct public interpretation; shared deity still
        # permits disagreement and grants no automatic trust.
    async with db.unit_of_work() as s:
        char = await s.get(Character, world.kael_id)
        await BeliefService(s).set_character_belief(
            char, _belief("tyr", interpretation="Mercy before strict law"),
            cleric_deity_key=None, cleric_domain=None,
        )
    async with db.session() as s:
        service = ReligiousInteractionService(s)
        context = await service.build_context(
            campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
            character_id=world.kael_id,
        )
        outcomes = await service.evaluate_special_interactions(context)
        disagreement = next(
            item for item in outcomes if item.payload.get("direction") == "disagreement"
        )
        assert disagreement.payload["trust_delta"] == 0


async def test_ordinary_follower_has_less_religious_knowledge_than_priest(db):
    world = await _faith_people(db, player_deity="oghma", npc_deity="oghma")
    async with db.unit_of_work() as s:
        npc = await s.get(NPC, world.guard_npc_id)
        ordinary = _belief("oghma", role=ReligiousRole.ORDINARY_FOLLOWER)
        await BeliefService(s).set_npc_belief(npc, ordinary)
    async with db.session() as s:
        context = await ReligiousInteractionService(s).build_context(
            campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
            character_id=world.kael_id,
        )
        assert context.npc_religious_knowledge == "CULTURAL"
        assert context.npc_religious_role == "ORDINARY_FOLLOWER"


async def test_doctrine_behavior_memory_and_relationship_are_idempotent(db):
    world = await _faith_people(db, player_deity="torm", npc_deity="torm")
    behavior = ReligiousBehavior(
        memory_type="RELIGIOUS_PROMISE", summary="Kael promised to protect the temple",
        event_tags=("KEEP_PROMISE",), importance=70,
        relationship_deltas={"trust": 7, "respect": 5},
    )
    async with db.unit_of_work() as s:
        service = ReligiousInteractionService(s)
        await service.record_religious_behavior(
            campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
            character_id=world.kael_id, source_event_id="promise-1", behavior=behavior,
        )
        await service.record_religious_behavior(
            campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
            character_id=world.kael_id, source_event_id="promise-1", behavior=behavior,
        )
    async with db.session() as s:
        memories = (await s.execute(select(NPCMemory).where(
            NPCMemory.event_id == "promise-1"))).scalars().all()
        relationship = (await s.execute(select(NPCRelationship).where(
            NPCRelationship.npc_id == world.guard_npc_id,
            NPCRelationship.entity_ref == f"character:{world.kael_id}",
        ))).scalar_one()
        assert len(memories) == 1
        assert relationship.trust == 7 and relationship.respect == 5


async def test_protection_and_desecration_create_meaningful_memories(db):
    world = await _faith_people(db, player_deity="tyr", npc_deity="tyr")
    async with db.unit_of_work() as s:
        service = ReligiousInteractionService(s)
        await service.record_religious_behavior(
            campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
            character_id=world.kael_id, source_event_id="protect-1",
            behavior=ReligiousBehavior(
                "TEMPLE_PROTECTED", "Kael protected the temple", importance=90,
                valence=3, relationship_deltas={"trust": 20, "respect": 25},
            ),
        )
        await service.record_religious_behavior(
            campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
            character_id=world.kael_id, source_event_id="desecrate-1",
            behavior=ReligiousBehavior(
                "SHRINE_DESECRATED", "Kael desecrated the shrine", importance=95,
                valence=-3, relationship_deltas={"trust": -30, "anger": 30},
            ),
        )
    async with db.session() as s:
        memories = (await s.execute(select(NPCMemory).where(
            NPCMemory.npc_id == world.guard_npc_id))).scalars().all()
        assert {item.memory_type for item in memories} == {"TEMPLE_PROTECTED", "SHRINE_DESECRATED"}


async def test_religious_interaction_never_rewrites_objective_canon(db):
    world = await _faith_people(db, player_deity="mystra", npc_deity="mystra")
    async with db.unit_of_work() as s:
        canon = CampaignCanonRecord(
            campaign_id=world.campaign_id, category="religion",
            fact="Owner-authored temple history", provenance="IMPORTED_CANON",
            data={"immutable_detail": "keep exactly"},
        )
        s.add(canon)
        await s.flush()
        canon_id = canon.id
        await ReligiousInteractionService(s).record_religious_behavior(
            campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
            character_id=world.kael_id, source_event_id="canon-safe-1",
            behavior=ReligiousBehavior(
                "SACRED_OBJECT_RETURNED", "Kael returned the sacred object",
                importance=80, relationship_deltas={"respect": 10},
            ),
        )
    async with db.session() as s:
        canon = await s.get(CampaignCanonRecord, canon_id)
        assert canon.fact == "Owner-authored temple history"
        assert canon.data == {"immutable_detail": "keep exactly"}
        assert canon.provenance == "IMPORTED_CANON"


async def _temple_world(db):
    world = await _faith_people(db, player_deity="tyr", npc_deity="tyr")
    async with db.unit_of_work() as s:
        faction = Threat(campaign_id=world.campaign_id, name="Order of Test", goal="Serve")
        s.add(faction)
        await s.flush()
        policy = TemplePolicy(
            key="test-temple", name="Test Temple", deity_key="tyr",
            location_id=world.location_id, faction_id=faction.id,
            public_access=True, member_access=True, clergy_access=True,
            emergency_sanctuary=False, provenance="OWNER_APPROVED_TEST",
            services=(
                TempleServicePolicy(kind=TempleServiceKind.RELIGIOUS_EDUCATION, price={"gp": 2}),
                TempleServicePolicy(kind=TempleServiceKind.HEALING, price={}),
                TempleServicePolicy(kind=TempleServiceKind.RITUAL, price={"gp": 1},
                                    required_area=TempleArea.RESTRICTED_ARCHIVE),
            ),
        )
        await ReligiousInteractionService(s).register_temple(world.campaign_id, policy)
        char = await s.get(Character, world.kael_id)
        await WalletService(s).apply(
            character_id=char.id, amounts={"gp": 5}, transaction_type="TEST",
            idempotency_key="temple-seed",
        )
        return world, faction.id


async def test_temple_policy_access_and_paid_service_use_existing_wallet(db):
    world, _ = await _temple_world(db)
    async with db.unit_of_work() as s:
        service = ReligiousInteractionService(s)
        public = await service.decide_temple_access(
            campaign_id=world.campaign_id, temple_key="test-temple",
            character_id=world.kael_id, area=TempleArea.PUBLIC,
        )
        archive = await service.decide_temple_access(
            campaign_id=world.campaign_id, temple_key="test-temple",
            character_id=world.kael_id, area=TempleArea.RESTRICTED_ARCHIVE,
        )
        assert public.allowed and not archive.allowed
        balance = await service.purchase_temple_service(
            campaign_id=world.campaign_id, temple_key="test-temple",
            character_id=world.kael_id,
            service_kind=TempleServiceKind.RELIGIOUS_EDUCATION,
            idempotency_key="temple-service-1",
        )
        assert balance["gp"] == 3
        with pytest.raises(ValidationError, match="free service"):
            await service.purchase_temple_service(
                campaign_id=world.campaign_id, temple_key="test-temple",
                character_id=world.kael_id, service_kind=TempleServiceKind.HEALING,
                idempotency_key="temple-service-free",
            )


async def test_access_and_faction_reputation_persist_and_are_campaign_scoped(db):
    world, faction_id = await _temple_world(db)
    async with db.unit_of_work() as s:
        service = ReligiousInteractionService(s)
        await service.grant_temple_access(
            campaign_id=world.campaign_id, temple_key="test-temple",
            character_id=world.kael_id, areas=(TempleArea.RESTRICTED_ARCHIVE,),
            source_event_id="permission-1",
        )
        assert await service.change_faction_reputation(
            campaign_id=world.campaign_id, faction_id=faction_id,
            character_id=world.kael_id, delta=12, source_event_id="rep-1",
        ) == 12
        # Duplicate delivery must not apply the relationship/reputation delta twice.
        assert await service.change_faction_reputation(
            campaign_id=world.campaign_id, faction_id=faction_id,
            character_id=world.kael_id, delta=12, source_event_id="rep-1",
        ) == 12
    async with db.session() as s:  # fresh session simulates process restart
        decision = await ReligiousInteractionService(s).decide_temple_access(
            campaign_id=world.campaign_id, temple_key="test-temple",
            character_id=world.kael_id, area=TempleArea.RESTRICTED_ARCHIVE,
        )
        assert decision.allowed
    async with db.session() as s:
        with pytest.raises(Exception, match="not found in campaign"):
            await ReligiousInteractionService(s).change_faction_reputation(
                campaign_id="different-campaign", faction_id=faction_id,
                character_id=world.kael_id, delta=1, source_event_id="leak",
            )


async def test_ao_belief_context_never_creates_cleric_mechanics(db):
    world = await _faith_people(db, player_deity="ao", npc_deity="ao")
    async with db.session() as s:
        char = await s.get(Character, world.kael_id)
        context = await ReligiousInteractionService(s).build_context(
            campaign_id=world.campaign_id, npc_id=world.guard_npc_id,
            character_id=world.kael_id,
        )
        assert context.shared_deity
        assert char.cleric_deity_key is None and char.cleric_domain is None
        assert not await FaithService(s).grants_cleric_powers(world.campaign_id, "ao")
