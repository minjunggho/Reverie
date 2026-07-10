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

async def walk_nara_creation(table, author="owner-1", name="DM"):
    """Stage A conversation + full Stage B build for Nara the rogue. Returns the
    final reveal BridgeResult."""
    await table.send("!rv character", author=author, name=name)
    r = await table.send("อยากเป็นผู้หญิงที่โตมากับโจร ไม่ค่อยพูด ใช้มีด แล้วชอบโกหกคน",
                         author=author, name=name)
    assert "?" in r.responses[0].content                    # one focused question
    await table.send("โตในซอกตลาดล่าง อยากมีที่ของตัวเอง", author=author, name=name)
    r = await table.send("เธอปากไม่ตรงกับใจ ไว้ใจใครยาก ชื่อ Nara", author=author, name=name)
    # STAGE A reflection: facts heard, no mechanics decided yet.
    assert "Nara" in (r.responses[0].title or "")
    assert r.responses[0].choices
    assert "HP" not in str(r.responses[0].data)             # nothing auto-built

    # STAGE B — the player chooses everything; the engine only recommends.
    r = await table.send("✅ ใช่ นี่แหละตัวข้า", author=author, name=name)
    assert "Class" in (r.responses[0].title or "")
    assert "แนะนำ" in r.responses[0].content                # a ⭐ recommendation exists
    r = await table.send("นักย่องเบา (rogue)", author=author, name=name)
    assert "Species" in (r.responses[0].title or "")
    r = await table.send("มนุษย์ (human)", author=author, name=name)
    assert "Background" in (r.responses[0].title or "")
    r = await table.send("อดีตคนนอกกฎหมาย (criminal)", author=author, name=name)
    assert "Standard Array" in r.responses[0].content
    r = await table.send("ใช้แบบแนะนำ", author=author, name=name)
    assert "+2" in r.responses[0].choices[0]                # background ASI choice
    r = await table.send("+2 DEX, +1 INT", author=author, name=name)
    # Class skills: rogue picks 4 (criminal's two are excluded from options).
    assert "เลือกแล้ว 0 / 4" in r.responses[0].content
    for skill in ("ผาดโผน (acrobatics)", "สังเกตการณ์ (perception)",
                  "ลวงหลอก (deception)", "อ่านใจคน (insight)"):
        r = await table.send(skill, author=author, name=name)
    # Human Skillful: one more skill of any kind.
    assert "หัวไว" in (r.responses[0].title or "")
    r = await table.send("สืบค้น (investigation)", author=author, name=name)
    # Rogue Expertise: two of the proficient skills.
    assert "Expertise" in (r.responses[0].title or "")
    r = await table.send("ลวงหลอก (deception)", author=author, name=name)
    r = await table.send("มือไว (sleight_of_hand)", author=author, name=name)
    # Review card, then finalize.
    assert "ตรวจทาน" in (r.responses[0].title or "")
    return await table.send("✅ สร้างเลย", author=author, name=name)


async def test_guided_creation_conversation_produces_hooks(db, provider):
    world = await build_world(db)
    table = Table(db, provider)
    r = await walk_nara_creation(table)
    assert r.responses[0].kind == MessageKind.CHARACTER_REVEAL

    async with db.session() as s:
        nara = (await s.execute(
            select(Character).where(Character.name == "Nara")
        )).scalar_one()
        assert nara.char_class == "rogue"                   # chosen, not assumed
        assert nara.species == "human" and nara.background == "criminal"
        # Recommended array for rogue (dex/int/con primary) + ASI +2 DEX +1 INT.
        assert nara.dex_score == 17 and nara.int_score == 15 and nara.con_score == 13
        # Skills: criminal 2 + class 4 + human skillful 1 = 7, no duplicates.
        assert len(nara.proficiencies) == 7
        assert "stealth" in nara.proficiencies              # from criminal background
        assert nara.expertise == ["deception", "sleight_of_hand"]
        assert nara.save_proficiencies == ["dex", "int"]
        assert nara.max_hp == 8 + 1                         # d8 + CON(+1)
        hooks = nara.hooks or {}
        assert hooks.get("concept") and hooks.get("desire") and hooks.get("flaw")
        # Grant provenance: "where did I get stealth?" is answerable.
        from app.models.progression import CharacterGrant
        stealth_grant = (await s.execute(
            select(CharacterGrant).where(CharacterGrant.character_id == nara.id,
                                         CharacterGrant.key == "stealth")
        )).scalar_one()
        assert stealth_grant.source_type == "BACKGROUND"
        # Starting gear was granted (class + background equipment).
        from app.services.campaigns.inventory_service import InventoryService
        items = await InventoryService(s).list_inventory(nara.id)
        assert len(items) >= 5


async def test_sheet_v2_and_skill_explanation(db, provider):
    """The sheet exposes real capabilities; `!rv skill` answers 'ทำไมถึง +N?'."""
    world = await build_world(db)
    table = Table(db, provider)
    await walk_nara_creation(table)

    r = await table.send("!rv sheet", author="owner-1", name="DM")
    sheet = r.responses[0]
    names = [f["name"] for f in sheet.data["fields"]]
    assert any("Initiative" in n for n in names)
    assert any("เซฟวิ่งโธรว์" in n for n in names)
    assert any("Passive Perception" in n for n in names)
    assert any("Hit Dice" in n for n in names)
    skills_field = next(f for f in sheet.data["fields"] if "ทักษะถนัด" in f["name"])
    assert "★" in skills_field["value"]                     # expertise marked

    # 'ทำไม deception +3?' — CHA -1, Expertise +4 (prof 2 doubled), explained.
    r = await table.send("!rv skill deception", author="owner-1", name="DM")
    body = r.responses[0].content
    assert "+3" in (r.responses[0].title or "")
    assert "Expertise +4" in body and "CHA -1" in body

    # A non-caster gets a graceful spells answer.
    r = await table.send("!rv spells", author="owner-1", name="DM")
    assert "ไม่ใช่ผู้ใช้เวท" in r.responses[0].content


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
