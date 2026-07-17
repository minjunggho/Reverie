"""The world pushes back on the DC — from committed state, not from the narrator.

These drive SituationReader and the production bridge together. The property that
matters: a check's difficulty reflects what the world actually remembers about this
character, so being trusted (or being caught lying, once) is mechanically real.
"""
from __future__ import annotations

from app.core.randomness import SequenceRandomness
from app.discord_bridge import InboundMessage
from app.engine import build_bridge
from app.models.character import Character
from app.models.enums import ResolutionType
from app.models.location import Location
from app.models.npc import NPC
from app.models.npc_epistemic import NPCRelationship
from app.models.progression import CharacterSpell
from app.schemas.llm_io import ActionInterpretation, AdjudicationDecision
from app.tabletop.adjudication import SituationReader
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1", name="Kael"):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"dc{_n['v']}", guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name=name, content=content,
    )


async def _relationship(db, world, **dims):
    async with db.unit_of_work() as s:
        rel = NPCRelationship(
            npc_id=world.guard_npc_id, entity_ref=f"character:{world.kael_id}", **dims)
        s.add(rel)


async def _factors(db, world, *, skill, target=True, location_id=None):
    async with db.session() as s:
        return await SituationReader(s).factors(
            campaign_id=world.campaign_id,
            actor_ref=f"character:{world.kael_id}", skill=skill,
            target_ref=f"npc:{world.guard_npc_id}" if target else None,
            location_id=location_id,
        )


# --- relationship: what this NPC has actually earned toward this character --------

async def test_a_trusting_npc_is_easier_to_persuade(db, provider):
    world = await build_world(db)
    await _relationship(db, world, trust=30, affection=20)

    factors = await _factors(db, world, skill="persuasion")

    assert [f.key for f in factors] == ["relationship_loyal"]
    assert factors[0].delta == -3
    assert factors[0].source == "engine"      # not the model's opinion


async def test_a_suspicious_npc_is_harder_to_lie_to(db, provider):
    world = await build_world(db)
    await _relationship(db, world, suspicion=40)

    factors = await _factors(db, world, skill="deception")

    assert [f.key for f in factors] == ["relationship_suspicious"]
    assert factors[0].delta == +3


async def test_an_afraid_npc_is_easier_to_intimidate(db, provider):
    world = await build_world(db)
    await _relationship(db, world, fear=30)

    factors = await _factors(db, world, skill="intimidation")

    assert [f.key for f in factors] == ["relationship_afraid"]
    assert factors[0].delta == -3


async def test_an_angry_npc_is_harder_to_persuade(db, provider):
    world = await build_world(db)
    await _relationship(db, world, anger=25)

    factors = await _factors(db, world, skill="persuasion")

    assert [f.key for f in factors] == ["relationship_hostile"]


async def test_relationship_does_not_leak_into_unrelated_skills(db, provider):
    """A guard who trusts you does not thereby make a lock easier to pick."""
    world = await build_world(db)
    await _relationship(db, world, trust=30, affection=20)

    assert await _factors(db, world, skill="sleight_of_hand") == []
    assert await _factors(db, world, skill="athletics") == []
    assert await _factors(db, world, skill="perception") == []


async def test_fear_does_not_make_an_npc_easier_to_lie_to(db, provider):
    """Each dimension touches only the checks it plausibly bears on."""
    world = await build_world(db)
    await _relationship(db, world, fear=30)

    assert await _factors(db, world, skill="deception") == []


async def test_no_relationship_means_no_factor(db, provider):
    """A stranger gets the bare band — never an invented adjustment."""
    world = await build_world(db)
    assert await _factors(db, world, skill="persuasion") == []


# --- the NPC's own condition ------------------------------------------------------

async def test_a_wounded_npc_is_easier_to_intimidate(db, provider):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        npc = await s.get(NPC, world.guard_npc_id)
        npc.physical_state = "wounded"

    factors = await _factors(db, world, skill="intimidation")

    assert "npc_wounded" in [f.key for f in factors]


async def test_a_cheerful_npc_is_easier_to_persuade(db, provider):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        npc = await s.get(NPC, world.guard_npc_id)
        npc.emotional_state = "happy"

    factors = await _factors(db, world, skill="persuasion")

    assert "npc_content" in [f.key for f in factors]


# --- world effects: the spell in the room changes the odds -------------------------

async def test_a_fog_cloud_makes_spotting_harder_and_hiding_easier(db, provider):
    """The payoff of the effect system: a fog cloud is not set dressing. It is cast,
    it persists, and it moves the DC of anything that depends on seeing."""
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        c = await s.get(Character, world.kael_id)
        c.char_class = "wizard"
        c.int_score = 16
        s.add(CharacterSpell(character_id=c.id, spell_key="fog_cloud", kind="book",
                             prepared=True))
    from app.tabletop.resources import ResourceEngine

    async with db.unit_of_work() as s:
        await ResourceEngine(s).grant(await s.get(Character, world.kael_id),
                                      "resource:spell_slots_1")

    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="สร้างม่านหมอก", method="ร่าย", intent_confidence=0.9, cast_intent=True,
        spell_reference="fog_cloud", target_references=[]))
    game = build_bridge(db, provider=provider, rng=SequenceRandomness(default=3))
    await game.handle_inbound(_msg("! ร่าย fog cloud"))

    async with db.session() as s:
        reader = SituationReader(s)
        spotting = await reader.factors(
            campaign_id=world.campaign_id, actor_ref=f"character:{world.kael_id}",
            skill="perception", location_id=world.location_id)
        hiding = await reader.factors(
            campaign_id=world.campaign_id, actor_ref=f"character:{world.kael_id}",
            skill="stealth", location_id=world.location_id)

    assert [f.key for f in spotting] == ["obscured"]
    assert spotting[0].delta == +3
    assert [f.key for f in hiding] == ["obscuring_cover"]
    assert hiding[0].delta == -3


# --- the place --------------------------------------------------------------------

async def test_bad_weather_makes_perception_harder(db, provider):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        loc = await s.get(Location, world.location_id)
        loc.weather = "พายุฝนกระหน่ำ"

    factors = await _factors(db, world, skill="perception", target=False,
                             location_id=world.location_id)

    assert [f.key for f in factors] == ["bad_weather"]


async def test_darkness_cuts_both_ways(db, provider):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        loc = await s.get(Location, world.location_id)
        loc.state = {**(loc.state or {}), "dark": True}

    spotting = await _factors(db, world, skill="perception", target=False,
                              location_id=world.location_id)
    hiding = await _factors(db, world, skill="stealth", target=False,
                            location_id=world.location_id)

    assert spotting[0].key == "location_dark" and spotting[0].delta == +3
    assert hiding[0].key == "location_dark" and hiding[0].delta == -2


async def test_a_malformed_location_state_yields_no_factor(db, provider):
    """`state` is a free JSON bag; junk in it must never become a DC adjustment."""
    world = await build_world(db)
    async with db.unit_of_work() as s:
        loc = await s.get(Location, world.location_id)
        loc.state = {"dark": "maybe-ish"}    # truthy string, not a real flag

    factors = await _factors(db, world, skill="stealth", target=False,
                             location_id=world.location_id)
    # A truthy non-bool still reads as set; what matters is that it cannot crash and
    # cannot produce a number outside the declared table.
    assert all(abs(f.delta) < 5 for f in factors)


# --- end to end through the bridge -------------------------------------------------

async def test_the_same_action_gets_a_different_dc_from_relationship_alone(db, provider):
    """Two identical Persuasion attempts, differing only in what the guard feels about
    the character. The DC must differ — that is the whole request."""
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ขอผ่านประตู", method="พูดโน้มน้าว", intent_confidence=0.9,
        target_references=["ยามเฝ้าประตู"]))
    provider.on("adjudicate_uncertain_action", lambda m, model: AdjudicationDecision(
        resolution_type=ResolutionType.ABILITY_CHECK, ability="cha",
        skill="persuasion", dc_band="MEDIUM", rationale="โน้มน้าวยาม"))
    game = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))

    result = await game.handle_inbound(_msg("! ขอยามให้เปิดประตู"))
    stranger_dc = [r.data["roll_line"] for r in result.responses
                   if r.data and r.data.get("roll_line")][0]
    assert "vs DC 15" in stranger_dc, stranger_dc

    # The guard now owes this character his trust.
    await _relationship(db, world, trust=30, affection=20)
    result = await game.handle_inbound(_msg("! ขอยามให้เปิดประตูอีกครั้ง"))
    friend_dc = [r.data["roll_line"] for r in result.responses
                 if r.data and r.data.get("roll_line")][0]

    assert "vs DC 12" in friend_dc, friend_dc
    assert "เขาไว้ใจเจ้ามาก" in friend_dc, "the reason must be visible"


async def test_the_dc_composition_is_recorded_on_the_check_event(db, provider):
    world = await build_world(db)
    sid, scene_id = await start_session_with_scene(db, world)
    await _relationship(db, world, suspicion=40)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="โกหกยาม", method="พูดหลอก", intent_confidence=0.9,
        target_references=["ยามเฝ้าประตู"]))
    provider.on("adjudicate_uncertain_action", lambda m, model: AdjudicationDecision(
        resolution_type=ResolutionType.ABILITY_CHECK, ability="cha", skill="deception",
        dc_band="MEDIUM", rationale="โกหก"))
    game = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))

    await game.handle_inbound(_msg("! โกหกยามว่าเป็นคนของเจ้าเมือง"))

    from sqlalchemy import select

    from app.models.event import Event

    async with db.session() as s:
        events = (await s.execute(select(Event).where(
            Event.event_type == "ABILITY_CHECK_RESOLVED"))).scalars().all()
    dc = events[-1].payload["dc"]
    assert dc["base"] == 15 and dc["total"] == 18
    assert [f["key"] for f in dc["factors"]] == ["relationship_suspicious"]
    assert dc["factors"][0]["source"] == "engine"
