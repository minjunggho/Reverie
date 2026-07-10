"""The dice ritual (§28) + failure-with-teeth via authored fragments (§F).

PLAYER_CLICK (production default): a visible check pauses at CHECK_PROMPT; the
server rolls only when the player taps; ROLL and NARRATION are separate messages.
Fragments: the model may only reveal clue text AUTHORED on the scene.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import InboundMessage
from app.engine import build_bridge
from app.models.campaign import Campaign
from app.models.enums import ConsequenceClass, EventType
from app.models.event import Event
from app.models.scene import Scene
from app.presentation import MessageKind
from app.schemas.llm_io import ConsequenceProposal, ProposedDelta
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1"):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"d{_n['v']}", guild_id="g", channel_id="chan-1",
        author_discord_id=author, author_display_name="กี้", content=content,
    )


async def _ritual_world(db):
    world = await build_world(db)
    session_id, scene_id = await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        campaign = await s.get(Campaign, world.campaign_id)
        campaign.config = {**campaign.config, "dice_mode": "PLAYER_CLICK"}
    return world, session_id, scene_id


async def test_check_pauses_until_the_player_rolls(db, provider):
    world, _, _ = await _ritual_world(db)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))

    r = await bridge.handle_inbound(_msg("! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    assert r.responses[0].kind == MessageKind.CHECK_PROMPT
    assert r.responses[0].choices == ["🎲 ทอย d20"]
    assert "Stealth" in (r.responses[0].title or "")
    assert "+5" in r.responses[0].content                 # modifier shown, DC hidden
    assert "15" not in r.responses[0].content
    # NOTHING has been rolled or committed yet.
    async with db.session() as s:
        count = (await s.execute(select(func.count(Event.id)).where(
            Event.event_type == EventType.ABILITY_CHECK_RESOLVED.value))).scalar_one()
        assert count == 0

    # A stray chat message doesn't roll; the table waits.
    r = await bridge.handle_inbound(_msg("ตื่นเต้นชะมัด"))
    assert "ลูกเต๋ายังรอ" in r.responses[0].content

    # The tap rolls on the SERVER; roll and narration arrive as separate objects.
    r = await bridge.handle_inbound(_msg("🎲 ทอย d20"))
    assert r.state_mutated is True
    assert r.responses[0].kind == MessageKind.CHECK_RESOLUTION
    assert "16 + 5 = 21" in r.responses[0].data["roll_line"]
    assert r.responses[1].kind == MessageKind.SCENE_FRAME
    async with db.session() as s:
        check = (await s.execute(select(Event).where(
            Event.event_type == EventType.ABILITY_CHECK_RESOLVED.value))).scalar_one()
        assert check.mechanical_changes["natural_roll"] == 16


async def test_pending_check_can_be_cancelled_or_replaced(db, provider):
    world, _, scene_id = await _ritual_world(db)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16, 12]))

    await bridge.handle_inbound(_msg("! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    r = await bridge.handle_inbound(_msg("ยกเลิก"))
    assert "ยังไม่มีอะไรเกิดขึ้น" in r.responses[0].content
    async with db.session() as s:
        assert (await s.get(Scene, scene_id)).pending_action is None

    # A new `!` action while a fresh check is pending replaces it cleanly.
    await bridge.handle_inbound(_msg("! ผมย่องไปดูหน้าต่างอีกครั้ง ไม่ให้ยามเห็น"))
    r = await bridge.handle_inbound(_msg("! ผมเดินไปเปิดประตู"))
    # The mundane door auto-succeeds (no ritual for automatic outcomes).
    assert r.state_mutated is True and "outcome=success" in r.note


async def test_auto_mode_resolves_immediately(db, provider):
    world = await build_world(db)          # factory default: AUTO
    await start_session_with_scene(db, world)
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([16]))
    r = await bridge.handle_inbound(_msg("! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น"))
    assert r.state_mutated is True
    assert r.responses[0].kind == MessageKind.CHECK_RESOLUTION


async def test_failed_check_reveals_only_authored_fragments(db, provider):
    world = await build_world(db)
    _, scene_id = await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        scene = await s.get(Scene, scene_id)
        scene.allowed_clues = ["...ไม่ใช่ของมนุษย์", "ต้องส่งมอบก่อนพระจันทร์เต็มดวง"]

    provider.on("plan_consequence", lambda m, model: ConsequenceProposal(
        consequence_class=ConsequenceClass.FAILURE_WITH_CONSEQUENCE,
        deltas=[
            ProposedDelta(kind="reveal_fragment", payload={"text": "...ไม่ใช่ของมนุษย์"}),
            # Hallucinated lore — MUST be rejected:
            ProposedDelta(kind="reveal_fragment", payload={"text": "กษัตริย์คือปีศาจ"}),
        ],
    ))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([3]))  # failure
    r = await bridge.handle_inbound(_msg("! ผมแอบฟังต่อไป"))

    blob = str(r.responses[0].data)
    assert "ไม่ใช่ของมนุษย์" in blob                       # authored fragment surfaced
    assert "กษัตริย์คือปีศาจ" not in blob                  # invention rejected
    async with db.session() as s:
        gained = (await s.execute(select(Event).where(
            Event.event_type == EventType.KNOWLEDGE_GAINED.value))).scalars().all()
        assert len(gained) == 1
        assert gained[0].payload["fragment"] == "...ไม่ใช่ของมนุษย์"


async def test_established_referent_needs_no_clarification(db, provider):
    """The playtest failure: '! แอบฟังต่อไป' with an established conversation must
    NOT trigger 'ฟังเรื่องอะไร?'. Even if the adjudicator eagerly asks, the engine
    gate suppresses it when the interpreter found the intent complete."""
    from app.models.enums import DifficultyBand, ResolutionType
    from app.schemas.llm_io import ActionInterpretation, AdjudicationDecision

    world = await build_world(db)
    await start_session_with_scene(db, world)
    provider.on("interpret_committed_action", lambda m, model: ActionInterpretation(
        goal="ฟังบทสนทนาเรื่องวัตถุประหลาดต่อ", method="แอบฟังกลมกลืนกับเสียงรอบตัว",
        target_references=["บทสนทนา"], intent_confidence=0.9, missing_information=[],
    ))
    provider.on("adjudicate_uncertain_action", lambda m, model: AdjudicationDecision(
        needs_clarification=True,                       # the eager model
        clarification_question="อยากฟังรายละเอียดเรื่องอะไรเป็นพิเศษ?",
        resolution_type=ResolutionType.ABILITY_CHECK, ability="wis", skill="perception",
        dc_band=DifficultyBand.MEDIUM,
    ))
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness([9]))
    r = await bridge.handle_inbound(_msg("! แอบฟังต่อไป"))
    # No clarification — the check simply proceeds (AUTO world -> resolved).
    assert all("เรื่องอะไร" not in m.content for m in r.responses)
    assert r.state_mutated is True
