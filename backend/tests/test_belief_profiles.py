"""Phase 2 faith: shared profiles, creation, Cleric rules, NPCs, and privacy."""
from __future__ import annotations

import copy
import json
import pytest
from sqlalchemy import select

from app.core.errors import ConflictError
from app.db.session import Database
from app.discord_bridge import AdminBridge, InboundMessage
from app.engine import build_bridge
from app.models.character import Character
from app.models.character_draft import CharacterDraft
from app.models.npc import NPC
from app.npcs import NPCBeliefContext, NPCBeliefGenerator, NPCService
from app.rules_content.faith_registry import FaithContentError, get_faith_registry
from app.schemas.belief import (
    BeliefProfile,
    BeliefSource,
    BeliefStance,
    BeliefVisibility,
    DevotionLevel,
    ReligiousKnowledgeLevel,
    ReligiousRole,
)
from app.services.beliefs import BeliefService
from app.services.campaigns.build_flow import BELIEF_FINISH, BELIEF_SKIP
from app.services.campaigns.canon_import import CanonImportService
from app.services.faith import FaithService
from app.services.views import build_belief_fields
from tests.support.factories import build_world


async def _activate(db, campaign_id: str) -> None:
    async with db.unit_of_work() as session:
        await FaithService(session).activate_pantheon(campaign_id, "forgotten_realms")


def _profile(
    *,
    deity: str | None = "tyr",
    stance: BeliefStance = BeliefStance.BELIEVER,
    visibility: BeliefVisibility = BeliefVisibility.PUBLIC,
    source: BeliefSource = BeliefSource.PLAYER_AUTHORED,
    **updates,
) -> BeliefProfile:
    return BeliefProfile(
        primary_deity_key=deity,
        stance=stance,
        devotion=DevotionLevel.ORDINARY if deity else DevotionLevel.NONE,
        visibility=visibility,
        source=source,
        provenance="TEST",
        **updates,
    )


def test_typed_profile_supports_no_deity_former_secret_and_multiple():
    agnostic = _profile(deity=None, stance=BeliefStance.AGNOSTIC)
    former = _profile(
        deity=None, stance=BeliefStance.FORMER_BELIEVER,
        former_deity_key="tyr", doubt="I no longer trust the church",
    )
    secret = _profile(
        visibility=BeliefVisibility.SECRET,
        stance=BeliefStance.SECRET_BELIEVER,
    )
    multiple = _profile(
        stance=BeliefStance.MULTI_FAITH,
        secondary_deity_keys=("torm", "tymora"),
    )
    assert agnostic.primary_deity_key is None
    assert former.former_deity_key == "tyr"
    assert secret.visibility is BeliefVisibility.SECRET
    assert multiple.secondary_deity_keys == ("torm", "tymora")


async def test_character_belief_edit_persists_without_replacing_identity(db):
    world = await build_world(db)
    await _activate(db, world.campaign_id)
    async with db.unit_of_work() as session:
        char = await session.get(Character, world.kael_id)
        char.identity = {"culture": "Neverwinter", "family": "Mara"}
        service = BeliefService(session)
        await service.set_character_belief(char, _profile(), cleric_deity_key=None, cleric_domain=None)
        await service.set_character_belief(
            char, _profile(personal_reason="Justice matters"),
            cleric_deity_key=None, cleric_domain=None,
        )
    async with db.session() as session:
        char = await session.get(Character, world.kael_id)
        profile = await BeliefService(session).get_character_belief(char)
        assert profile.personal_reason == "Justice matters"
        assert char.identity == {"culture": "Neverwinter", "family": "Mara"}
        assert isinstance(char.belief_profile, dict)  # one embedded profile, no duplicate rows


async def test_inactive_pantheon_and_cross_campaign_deity_are_rejected(db):
    world = await build_world(db)
    async with db.session() as session:
        char = await session.get(Character, world.kael_id)
        with pytest.raises(FaithContentError, match="active pantheon"):
            await BeliefService(session).validate_profile(world.campaign_id, _profile())
        assert await FaithService(session).get_deity(world.campaign_id, "tyr") is None
        assert char.belief_profile is None


async def test_disabled_content_fails_safely_on_profile_read(db):
    world = await build_world(db)
    await _activate(db, world.campaign_id)
    async with db.unit_of_work() as session:
        char = await session.get(Character, world.kael_id)
        await BeliefService(session).set_character_belief(
            char, _profile(), cleric_deity_key=None, cleric_domain=None
        )
    async with db.unit_of_work() as session:
        await FaithService(session).deactivate_pantheon(
            world.campaign_id, "forgotten_realms"
        )
    async with db.session() as session:
        char = await session.get(Character, world.kael_id)
        with pytest.raises(FaithContentError, match="active pantheon"):
            await BeliefService(session).get_character_belief(char)


async def test_cleric_ao_rejected_domain_validated_and_fighter_may_worship(db):
    world = await build_world(db)
    await _activate(db, world.campaign_id)
    async with db.session() as session:
        service = BeliefService(session)
        with pytest.raises(FaithContentError, match="cleric_capable"):
            await service.validate_cleric_mechanics(
                world.campaign_id, char_class="cleric", deity_key="ao", domain="Knowledge"
            )
        with pytest.raises(FaithContentError, match="expected one of"):
            await service.validate_cleric_mechanics(
                world.campaign_id, char_class="cleric", deity_key="tyr", domain="Trickery"
            )
        deity, domain = await service.validate_cleric_mechanics(
            world.campaign_id, char_class="cleric", deity_key="tyr", domain="War"
        )
        assert (deity, domain) == ("tyr", "War")
        fighter = await session.get(Character, world.bront_id)
        await service.set_character_belief(
            fighter, _profile(deity="ao"), cleric_deity_key=None, cleric_domain=None
        )
        fighter.char_class = "cleric"
        await service.set_character_belief(
            fighter, _profile(), cleric_deity_key="tyr", cleric_domain="War"
        )
        await service.set_character_belief(
            fighter, _profile(personal_reason="edited without touching mechanics")
        )
        assert (fighter.cleric_deity_key, fighter.cleric_domain) == ("tyr", "War")


async def test_secret_belief_is_only_visible_to_owner(db):
    world = await build_world(db)
    await _activate(db, world.campaign_id)
    async with db.unit_of_work() as session:
        char = await session.get(Character, world.kael_id)
        await BeliefService(session).set_character_belief(
            char,
            _profile(
                stance=BeliefStance.SECRET_BELIEVER,
                visibility=BeliefVisibility.SECRET,
                owner_notes="Do not reveal the shrine",
            ),
            cleric_deity_key=None,
            cleric_domain=None,
        )
        assert build_belief_fields(char, owner_view=False) == []
        owner_fields = build_belief_fields(char, owner_view=True)
        assert any(field["name"] == "ความเชื่อส่วนตัว" for field in owner_fields)
        assert any("Do not reveal" in field["value"] for field in owner_fields)


def _review_build(step: str = "belief") -> dict:
    return {
        "name": "Nara",
        "identity": {"name": "Nara"},
        "_build": {
            "step": step,
            "belief_stage": "broad",
            "class": "rogue",
            "species": "human",
            "background": "criminal",
            "scores": {"str": 8, "dex": 17, "con": 13, "int": 12, "wis": 14, "cha": 10},
            "skills": ["stealth", "acrobatics", "perception", "investigation"],
            "expertise": ["stealth", "perception"],
            "component_token": "faith-test",
        },
    }


async def _seed_draft(db, world, data: dict) -> None:
    async with db.unit_of_work() as session:
        session.add(CharacterDraft(
            campaign_id=world.campaign_id, member_id=world.p1_member_id, data=data
        ))


async def test_creation_resolves_english_thai_and_resumes_belief_step(db, provider):
    world = await build_world(db)
    await _activate(db, world.campaign_id)
    await _seed_draft(db, world, _review_build())
    game = build_bridge(db, provider=provider)
    admin = AdminBridge(db, provider, creation_flow=game.creation_flow, session_zero=game.session_zero)
    resume = await admin.handle(InboundMessage(
        discord_message_id="faith-resume", guild_id="guild-1", channel_id="chan-1",
        author_discord_id="disc-p1", author_display_name="Nara", content="!rv resume",
    ))
    assert "ความเชื่อ" in (resume.responses[0].title or "")
    result = await game.creation_flow.handle_message(
        campaign_id=world.campaign_id, member_id=world.p1_member_id,
        channel_id="chan-1", text="Tyr",
    )
    assert "รายละเอียด" in (result.responses[0].title or "")
    thai_name = get_faith_registry().get_deity("tyr").name_th
    # Return to deity selection and prove the same campaign resolver accepts Thai.
    async with db.unit_of_work() as session:
        draft = (await session.execute(select(CharacterDraft).where(
            CharacterDraft.member_id == world.p1_member_id,
            CharacterDraft.status == "ACTIVE",
        ))).scalar_one()
        payload = copy.deepcopy(draft.data)
        payload["_build"]["belief_stage"] = "deity"
        payload["_build"]["belief_intent"] = "believer"
        draft.data = payload
    result = await game.creation_flow.handle_message(
        campaign_id=world.campaign_id, member_id=world.p1_member_id,
        channel_id="chan-1", text=thai_name,
    )
    assert "รายละเอียด" in (result.responses[0].title or "")
    result = await game.creation_flow.handle_message(
        campaign_id=world.campaign_id, member_id=world.p1_member_id,
        channel_id="chan-1", text=BELIEF_FINISH,
    )
    assert "ตรวจทาน" in (result.responses[0].title or "")
    await game.creation_flow.handle_message(
        campaign_id=world.campaign_id, member_id=world.p1_member_id,
        channel_id="chan-1", text="✅ สร้างเลย",
    )
    # A repeated confirmation cannot create another profile/character.
    await game.creation_flow.handle_message(
        campaign_id=world.campaign_id, member_id=world.p1_member_id,
        channel_id="chan-1", text="✅ สร้างเลย",
    )
    async with db.session() as session:
        created = list((await session.execute(
            select(Character).where(Character.name == "Nara")
        )).scalars())
        assert len(created) == 1
        assert (await BeliefService(session).get_character_belief(
            created[0]
        )).primary_deity_key == "tyr"


async def test_creation_belief_resume_survives_application_restart(tmp_path, provider):
    path = tmp_path / "belief-draft-restart.sqlite3"
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    first = Database(url, echo=False)
    await first.create_all()
    world = await build_world(first)
    await _activate(first, world.campaign_id)
    data = _review_build()
    data["_build"]["belief_stage"] = "deity"
    data["_build"]["belief_intent"] = "secret"
    await _seed_draft(first, world, data)
    await first.dispose()

    second = Database(url, echo=False)
    try:
        game = build_bridge(second, provider=provider)
        admin = AdminBridge(
            second, provider, creation_flow=game.creation_flow,
            session_zero=game.session_zero,
        )
        resumed = await admin.handle(InboundMessage(
            discord_message_id="faith-restart", guild_id="guild-1",
            channel_id="chan-1", author_discord_id="disc-p1",
            author_display_name="Nara", content="!rv resume",
        ))
        assert "เลือกเทพ" in (resumed.responses[0].title or "")
        await game.creation_flow.handle_message(
            campaign_id=world.campaign_id, member_id=world.p1_member_id,
            channel_id="chan-1", text="Tyr",
        )
        async with second.session() as session:
            draft = (await session.execute(select(CharacterDraft).where(
                CharacterDraft.member_id == world.p1_member_id,
                CharacterDraft.status == "ACTIVE",
            ))).scalar_one()
            profile = BeliefService.decode(draft.data["_build"]["belief_profile"])
            assert profile.stance is BeliefStance.SECRET_BELIEVER
            assert profile.visibility is BeliefVisibility.SECRET
    finally:
        await second.dispose()


async def test_creation_no_deity_and_adaptive_former_believer(db, provider):
    world = await build_world(db)
    await _activate(db, world.campaign_id)
    await _seed_draft(db, world, _review_build())
    game = build_bridge(db, provider=provider)
    result = await game.creation_flow.handle_message(
        campaign_id=world.campaign_id, member_id=world.p1_member_id,
        channel_id="chan-1", text=BELIEF_SKIP,
    )
    assert "ตรวจทาน" in (result.responses[0].title or "")

    async with db.unit_of_work() as session:
        draft = (await session.execute(select(CharacterDraft).where(
            CharacterDraft.member_id == world.p1_member_id,
            CharacterDraft.status == "ACTIVE",
        ))).scalar_one()
        payload = copy.deepcopy(draft.data)
        payload["_build"]["step"] = "belief"
        payload["_build"]["belief_stage"] = "broad"
        draft.data = payload
    result = await game.creation_flow.handle_message(
        campaign_id=world.campaign_id, member_id=world.p1_member_id,
        channel_id="chan-1",
        text="I was raised in a temple of Tyr but I no longer trust the church.",
    )
    assert "รายละเอียด" in (result.responses[0].title or "")
    async with db.session() as session:
        draft = (await session.execute(select(CharacterDraft).where(
            CharacterDraft.member_id == world.p1_member_id,
            CharacterDraft.status == "ACTIVE",
        ))).scalar_one()
        profile = BeliefService.decode(draft.data["_build"]["belief_profile"])
        assert profile.stance is BeliefStance.FORMER_BELIEVER
        assert profile.former_deity_key == "tyr"
        assert profile.doubt


async def test_creation_cleric_gate_rejects_ao_and_accepts_legal_domain(db, provider):
    world = await build_world(db)
    await _activate(db, world.campaign_id)
    data = _review_build()
    data["_build"]["class"] = "cleric"
    await _seed_draft(db, world, data)
    game = build_bridge(db, provider=provider)
    await game.creation_flow.handle_message(
        campaign_id=world.campaign_id, member_id=world.p1_member_id,
        channel_id="chan-1", text=BELIEF_SKIP,
    )
    rejected = await game.creation_flow.handle_message(
        campaign_id=world.campaign_id, member_id=world.p1_member_id,
        channel_id="chan-1", text="Ao",
    )
    assert "ไม่สามารถ" in rejected.responses[0].content
    await game.creation_flow.handle_message(
        campaign_id=world.campaign_id, member_id=world.p1_member_id,
        channel_id="chan-1", text="Tyr",
    )
    result = await game.creation_flow.handle_message(
        campaign_id=world.campaign_id, member_id=world.p1_member_id,
        channel_id="chan-1", text="War",
    )
    assert "ตรวจทาน" in (result.responses[0].title or "")


async def test_npc_generation_varies_and_priest_has_deep_knowledge(db):
    world = await build_world(db)
    await _activate(db, world.campaign_id)
    async with db.session() as session:
        generator = NPCBeliefGenerator(FaithService(session))
        proposals = [
            await generator.propose(world.campaign_id, NPCBeliefContext(name=f"Ordinary {i}"))
            for i in range(24)
        ]
        signatures = {
            proposal.profile.stance.value if proposal.profile else "NONE"
            for proposal in proposals
        }
        assert len(signatures) >= 3
        priest = await generator.propose(
            world.campaign_id,
            NPCBeliefContext(
                name="Justicar", profession="priest of Tyr",
                temple_connection="Temple of Tyr", religious_role=ReligiousRole.PRIEST,
            ),
        )
        assert priest.profile.primary_deity_key == "tyr"
        assert priest.profile.religious_role is ReligiousRole.PRIEST
        assert priest.profile.knowledge_level is ReligiousKnowledgeLevel.DEEP
        assert priest.profile.stance is BeliefStance.DEVOUT
    async with db.unit_of_work() as session:
        npc = await NPCService(session).create_npc(
            campaign_id=world.campaign_id,
            name="Temple Archivist",
            belief_context=NPCBeliefContext(
                name="Temple Archivist", profession="theologian of Oghma",
                temple_connection="Temple of Oghma",
                religious_role=ReligiousRole.THEOLOGIAN,
            ),
        )
        persisted = await BeliefService(session).get_npc_belief(npc)
        assert persisted.primary_deity_key == "oghma"
        assert persisted.knowledge_level is ReligiousKnowledgeLevel.DEEP


async def test_imported_npc_belief_outranks_generated_proposal(db):
    world = await build_world(db)
    await _activate(db, world.campaign_id)
    async with db.unit_of_work() as session:
        npc = await session.get(NPC, world.guard_npc_id)
        service = BeliefService(session)
        await service.set_npc_belief(
            npc, _profile(source=BeliefSource.AI_GENERATED)
        )
        imported = _profile(
            deity="torm", source=BeliefSource.IMPORTED_CANON,
            religious_role=ReligiousRole.TEMPLE_GUARD,
        )
        await service.set_npc_belief(npc, imported)
        with pytest.raises(ConflictError, match="IMPORTED_CANON"):
            await service.set_npc_belief(
                npc, _profile(source=BeliefSource.AI_GENERATED)
            )
        assert (await service.get_npc_belief(npc)).primary_deity_key == "torm"


async def test_canon_imported_npc_belief_is_persisted_as_imported(db):
    world = await build_world(db)
    await _activate(db, world.campaign_id)
    payload = {
        "version": 1,
        "locations": [{"key": "temple", "name": "Imported Temple"}],
        "npcs": [{
            "key": "canon_priest", "name": "Canon Priest",
            "location": "temple", "deity_reference": "Tyr",
            "religious_role": "PRIEST",
        }],
    }
    async with db.unit_of_work() as session:
        service = CanonImportService(session)
        draft = await service.create_draft(
            campaign_id=world.campaign_id,
            uploader_member_id=world.owner_member_id,
            filename="faith.json",
            data=json.dumps(payload).encode("utf-8"),
        )
        await service.approve(import_id=draft.id, campaign_id=world.campaign_id)
    async with db.session() as session:
        npc = (await session.execute(select(NPC).where(
            NPC.campaign_id == world.campaign_id,
            NPC.name == "Canon Priest",
        ))).scalar_one()
        profile = await BeliefService(session).get_npc_belief(npc)
        assert profile.primary_deity_key == "tyr"
        assert profile.source is BeliefSource.IMPORTED_CANON
        assert profile.religious_role is ReligiousRole.PRIEST
        assert profile.knowledge_level is ReligiousKnowledgeLevel.DEEP


async def test_restart_preserves_character_and_npc_beliefs(tmp_path):
    path = tmp_path / "belief-restart.sqlite3"
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    first = Database(url, echo=False)
    await first.create_all()
    world = await build_world(first)
    await _activate(first, world.campaign_id)
    async with first.unit_of_work() as session:
        char = await session.get(Character, world.kael_id)
        npc = await session.get(NPC, world.guard_npc_id)
        service = BeliefService(session)
        await service.set_character_belief(
            char, _profile(), cleric_deity_key=None, cleric_domain=None
        )
        await service.set_npc_belief(
            npc, _profile(deity="torm", source=BeliefSource.IMPORTED_CANON)
        )
    await first.dispose()

    second = Database(url, echo=False)
    try:
        async with second.session() as session:
            char = await session.get(Character, world.kael_id)
            npc = await session.get(NPC, world.guard_npc_id)
            assert (await BeliefService(session).get_character_belief(char)).primary_deity_key == "tyr"
            assert (await BeliefService(session).get_npc_belief(npc)).primary_deity_key == "torm"
    finally:
        await second.dispose()
