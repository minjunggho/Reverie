"""Experience-overhaul acceptance: presentation contract, guided creation, Session
Zero, error staging, views, NPC dialogue routing, and private-secret delivery."""
from __future__ import annotations

from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import AdminBridge, InboundMessage, is_admin_command
from app.engine import build_bridge
from app.models.campaign import Campaign
from app.models.character import Character
from app.models.enums import ConsequenceClass, MessageCategory, ProcessingStage
from app.models.knowledge import Secret
from app.models.processed_message import ProcessedMessage
from app.presentation import MessageKind
from app.schemas.llm_io import ClassificationResult, ConsequenceProposal, ProposedDelta
from tests.support.factories import build_world, start_session_with_scene

_n = {"v": 0}


def _msg(content, author="disc-p1", name="กี้"):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"x{_n['v']}", guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name=name, content=content,
    )


class Table:
    def __init__(self, db, provider, rng=None):
        self.game = build_bridge(db, provider=provider,
                                 rng=rng or SequenceRandomness(default=14))
        self.admin = AdminBridge(db, provider, creation_flow=self.game.creation_flow,
                                 session_zero=self.game.session_zero)

    async def send(self, content, author="disc-p1", name="กี้"):
        inbound = _msg(content, author=author, name=name)
        if is_admin_command(content):
            return await self.admin.handle(inbound)
        return await self.game.handle_inbound(inbound)


# --- presentation contract -----------------------------------------------------

async def test_welcome_is_context_aware(db, provider):
    table = Table(db, provider)
    world = await build_world(db)
    # A brand-new member with no character -> next step = create one.
    await table.send("!rv join", author="disc-p3", name="เอ็ม")
    r = await table.send("!rv", author="disc-p3", name="เอ็ม")
    assert r.responses[0].kind == MessageKind.REVERIE_WELCOME
    assert "!rv character" in r.responses[0].content
    # A member whose character exists -> next step points at the session instead.
    r2 = await table.send("!rv", author="disc-p1")
    assert "session start" in r2.responses[0].content


async def test_check_resolution_message_is_structured(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    table = Table(db, provider, rng=SequenceRandomness([16]))
    r = await table.send("! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น")
    out = r.responses[0]
    assert out.kind == MessageKind.CHECK_RESOLUTION
    assert "16 + 5 = 21" in out.data["roll_line"]          # committed numbers, visible
    assert out.data["decision_prompt"]                     # a next decision point
    assert "21" not in out.content                         # mechanics NOT in the prose


# --- error staging ---------------------------------------------------------------

async def test_pre_commit_failure_says_nothing_happened(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)

    def _boom(messages, model):
        raise RuntimeError("adjudicator exploded")

    provider.on("adjudicate_uncertain_action", _boom)
    table = Table(db, provider)
    r = await table.send("! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น")
    out = r.responses[0]
    assert out.kind == MessageKind.TECHNICAL_ERROR
    assert r.state_mutated is False
    assert "ยังไม่มีอะไรเกิดขึ้น" in out.content            # honest: nothing happened
    async with db.session() as s:
        pm = (await s.execute(select(ProcessedMessage))).scalars().first()
        assert pm.stage == ProcessingStage.FAILED.value    # marked for a clean retry


async def test_post_commit_narration_crash_restates_the_result(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)

    def _boom(messages, model):
        raise RuntimeError("narrator exploded")  # NOT an LLMError -> escapes the job

    provider.on("generate_dm_narration", _boom)
    table = Table(db, provider, rng=SequenceRandomness([16]))
    r = await table.send("! ผมย่องไปดูหน้าต่าง ไม่ให้ยามเห็น")
    out = r.responses[0]
    assert out.kind == MessageKind.TECHNICAL_ERROR
    assert r.state_mutated is True                          # the action COUNTED
    assert "21" in out.data["roll_line"]                    # factual result restated
    # And the invariant: exactly one committed check, never re-rolled.
    from app.models.enums import EventType
    from app.models.event import Event
    async with db.session() as s:
        checks = (await s.execute(
            select(Event).where(Event.event_type == EventType.ABILITY_CHECK_RESOLVED.value)
        )).scalars().all()
        assert len(checks) == 1


# --- guided character creation ------------------------------------------------------

async def test_guided_creation_conversation_produces_hooks(db, provider):
    world = await build_world(db)
    table = Table(db, provider)
    # p2's member has Bront already; use a fresh member: the owner creates one.
    r = await table.send("!rv character", author="owner-1", name="DM")
    assert r.responses[0].kind == MessageKind.CHARACTER_CREATION

    r = await table.send("อยากเป็นผู้หญิงที่โตมากับโจร ไม่ค่อยพูด ใช้มีด แล้วชอบโกหกคน",
                         author="owner-1", name="DM")
    assert r.responses[0].kind == MessageKind.CHARACTER_CREATION
    assert "?" in r.responses[0].content                    # exactly one focused question

    r = await table.send("โตในซอกตลาดล่าง อยากมีที่ของตัวเอง", author="owner-1", name="DM")
    r = await table.send("เธอปากไม่ตรงกับใจ ไว้ใจใครยาก ชื่อ Nara", author="owner-1", name="DM")
    # Confirm proposal with choices.
    assert r.responses[0].choices
    assert "Nara" in (r.responses[0].title or "")

    r = await table.send("✅ ใช่ นี่แหละตัวข้า", author="owner-1", name="DM")
    out = r.responses[0]
    assert out.kind == MessageKind.CHARACTER_REVEAL

    async with db.session() as s:
        nara = (await s.execute(
            select(Character).where(Character.name == "Nara")
        )).scalar_one()
        assert nara.char_class == "rogue"                   # mapped from the fantasy
        hooks = nara.hooks or {}
        assert hooks.get("concept") and hooks.get("desire") and hooks.get("flaw")
        # Starting gear was granted.
        from app.services.campaigns.inventory_service import InventoryService
        items = await InventoryService(s).list_inventory(nara.id)
        assert len(items) >= 3


async def test_creation_messages_never_hit_the_classifier(db, provider):
    world = await build_world(db)
    table = Table(db, provider)
    await table.send("!rv character", author="owner-1", name="DM")
    calls_before = [c for c in provider.calls if c[0] == "classify_table_message"]
    await table.send("อยากเป็นนักรบแก่ๆ ที่เลิกรบแล้ว", author="owner-1", name="DM")
    calls_after = [c for c in provider.calls if c[0] == "classify_table_message"]
    assert len(calls_after) == len(calls_before)            # routed to creation, not chat


# --- Session Zero ----------------------------------------------------------------------

async def test_session_zero_flow_stores_profile(db, provider):
    world = await build_world(db)
    table = Table(db, provider)
    r = await table.send("!rv setup", author="owner-1", name="DM")
    assert r.responses[0].choices                            # button-able question
    await table.send("มืด จริงจัง", author="owner-1", name="DM")
    await table.send("เน้นบทบาท คุยกับ NPC", author="owner-1", name="DM")
    await table.send("มี — ช่วยอธิบายกฎหน่อยนะ", author="owner-1", name="DM")
    r = await table.send("ไม่เอาเรื่องทรมานสัตว์", author="owner-1", name="DM")
    assert "พร้อมแล้ว" in r.responses[0].content
    async with db.session() as s:
        campaign = await s.get(Campaign, world.campaign_id)
        profile = campaign.config["profile"]
        assert profile["tone"] == "มืด จริงจัง"
        assert profile["assistance"] == "BEGINNER"
        assert profile["boundaries"] == ["ไม่เอาเรื่องทรมานสัตว์"]
        assert "setup_state" not in campaign.config          # flow closed


# --- NPC dialogue + private secrets ------------------------------------------------------

async def test_character_dialogue_routes_to_visible_npc(db, provider):
    world = await build_world(db)
    await start_session_with_scene(db, world)
    provider.push("classify_table_message", ClassificationResult(
        category=MessageCategory.CHARACTER_DIALOGUE, confidence=0.9,
    ))
    table = Table(db, provider)
    r = await table.send("“สวัสดีท่านยาม คืนนี้เงียบดีนะ”")
    out = r.responses[0]
    assert out.kind == MessageKind.NPC_DIALOGUE
    assert out.title == "ยามเฝ้าประตู"                       # speaks as the named NPC
    assert r.state_mutated is False


async def test_private_secret_is_delivered_privately_and_never_leaks(db, provider):
    world = await build_world(db)
    session_id, _ = await start_session_with_scene(db, world)
    async with db.unit_of_work() as s:
        secret = Secret(campaign_id=world.campaign_id,
                        fact="SECRET_ตราบนข้อมือยามคือตราโบสถ์เงิน")
        s.add(secret)
        await s.flush()
        secret_id = secret.id

    provider.on("plan_consequence", lambda m, model: ConsequenceProposal(
        consequence_class=ConsequenceClass.SUCCESS,
        deltas=[ProposedDelta(kind="reveal_secret", target=f"character:{world.kael_id}",
                              payload={"secret_id": secret_id})],
    ))
    table = Table(db, provider, rng=SequenceRandomness([18]))
    r = await table.send("! ผมแอบมองข้อมือของยามตอนเขาเปลี่ยนกะ")

    public = [m for m in r.responses if m.private_to_discord_id is None]
    private = [m for m in r.responses if m.private_to_discord_id is not None]
    assert len(private) == 1
    assert private[0].kind == MessageKind.PRIVATE_SECRET
    assert private[0].private_to_discord_id == world.p1_discord_id
    assert "SECRET_" in private[0].content
    assert all("SECRET_" not in m.content for m in public)   # no public leakage

    # And the recap (player-visible retrieval) cannot contain it either.
    from app.ai.jobs import SafeRecapGenerator
    async with db.session() as s:
        recap = await SafeRecapGenerator(provider).run(
            s, campaign_id=world.campaign_id, session_id=session_id)
    assert "SECRET_" not in recap.text


async def test_reveal_secret_cannot_invent_content(db, provider):
    """A hallucinated secret_id is rejected — only pre-authored secrets exist."""
    world = await build_world(db)
    await start_session_with_scene(db, world)
    provider.on("plan_consequence", lambda m, model: ConsequenceProposal(
        consequence_class=ConsequenceClass.SUCCESS,
        deltas=[ProposedDelta(kind="reveal_secret", target=f"character:{world.kael_id}",
                              payload={"secret_id": "made-up-by-the-model"})],
    ))
    table = Table(db, provider, rng=SequenceRandomness([18]))
    r = await table.send("! ผมมองหาความลับрอบตัว")
    assert all(m.kind != MessageKind.PRIVATE_SECRET for m in r.responses)
