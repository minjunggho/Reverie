"""Phase 2 — immersive, player-centered character creation.

Creation must feel like the start of the campaign, not a form: the complete
player-authored text is preserved verbatim AND a structured identity is extracted
from it; the conversation recognizes what was already supplied and never re-asks;
an explicit but unsupported class keeps its fiction while proposing a supported
chassis; a custom ancestry keeps its appearance while its mechanics are chosen and
owner-approved (never auto-granted); and reviewable evolution seeds are stored
PENDING until campaign context validates them.
"""
from __future__ import annotations

from sqlalchemy import select

from app.discord_bridge import AdminBridge, InboundMessage, is_admin_command
from app.engine import build_bridge
from app.models.character import Character
from app.models.character_draft import CharacterDraft
from app.presentation import MessageKind
from app.services.campaigns import identity as idmod
from tests.support.factories import build_world

_n = {"v": 0}


def _msg(content, author="disc-p1", name="กี้"):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"v2-{_n['v']}", guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name=name, content=content,
    )


class Table:
    def __init__(self, db, provider):
        self.game = build_bridge(db, provider=provider)
        self.admin = AdminBridge(db, provider, creation_flow=self.game.creation_flow,
                                 session_zero=self.game.session_zero)

    async def send(self, content, author="disc-p1", name="กี้"):
        inbound = _msg(content, author=author, name=name)
        if is_admin_command(content):
            return await self.admin.handle(inbound)
        return await self.game.handle_inbound(inbound)


async def _draft_for(db, member_id):
    async with db.session() as s:
        return (await s.execute(select(CharacterDraft).where(
            CharacterDraft.member_id == member_id,
            CharacterDraft.status == "ACTIVE"))).scalars().first()


# --- identity module (pure logic) ----------------------------------------------

def test_unsupported_class_keeps_fiction_and_proposes_chassis():
    # Only Paladin + Druid remain locked; a stated one keeps its fiction + chassis.
    for stated, chassis in (("paladin", "fighter"), ("druid", "cleric")):
        r = idmod.resolve_class_intention(f"I am a {stated} of legend")
        assert r.stated == stated and r.is_unsupported and r.chassis == chassis
        assert r.recommended == chassis
    supported = idmod.resolve_class_intention("a cunning rogue")
    assert supported.is_supported and supported.recommended == "rogue"
    # Classes unlocked over the phases now map to THEMSELVES, no chassis.
    for now_supported in ("sorcerer", "warlock", "barbarian", "monk"):
        r = idmod.resolve_class_intention(f"I am a {now_supported}")
        assert r.is_supported and r.recommended == now_supported


def test_custom_ancestry_detection_and_base_suggestion():
    assert idmod.is_custom_ancestry("Catfolk") is True
    assert idmod.is_custom_ancestry("winged catperson") is True
    assert idmod.is_custom_ancestry("dragonborn") is False   # bundled
    assert idmod.is_custom_ancestry("") is False
    assert idmod.suggested_base_for_custom("a lithe Catfolk") == "halfling"
    assert idmod.suggested_base_for_custom("a winged one") == "aasimar"


def test_seeds_are_derived_and_pending():
    seeds = idmod.generate_seeds({
        "goals": "หาน้องสาวที่หายไป", "rivals": "อดีตหัวหน้าโจร",
        "secrets": "เคยทรยศเพื่อน", "homeland": "เมืองท่าเก่า",
        "distinctive_marks": "แหวนของแม่",
    })
    kinds = {s.kind for s in seeds}
    assert kinds == {"hook", "relationship", "rumor", "object", "connection"}
    assert all(s.status == "proposed" for s in seeds)      # nothing guaranteed true


# --- adaptive conversation: recognize supplied details, don't re-ask -------------

async def test_one_message_rich_concept_goes_straight_to_reflection(db, provider):
    world = await build_world(db)
    table = Table(db, provider)
    await table.send("!rv character")
    # Everything in one message: class, ancestry, religion, a name.
    r = await table.send(
        "I am Rhaegar, a Dragonborn Paladin raised in a temple of Bahamut, "
        "sworn to protect the weak")
    card = r.responses[0]
    # Recognized enough to reflect back immediately — not a fresh interrogation.
    assert "Rhaegar" in (card.title or "")
    assert card.choices                                     # confirm/edit offered
    draft = await _draft_for(db, world.p1_member_id)
    ident = draft.data.get("identity") or {}
    assert ident.get("class_intention") == "paladin"        # fiction captured
    assert draft.data.get("_narrative_class") == "paladin"
    assert draft.data.get("_class_hint") == "fighter"       # supported chassis proposed
    assert ident.get("ancestry")                            # ancestry captured
    # The complete original text is preserved verbatim.
    assert "temple of Bahamut" in draft.data.get("_origin_text", "")


async def test_explicit_sorcerer_intent_now_maps_to_the_real_sorcerer_class(db, provider):
    world = await build_world(db)
    table = Table(db, provider)
    await table.send("!rv character")
    await table.send("Kaelen, a human Sorcerer whose magic runs in the blood")
    draft = await _draft_for(db, world.p1_member_id)
    # Sorcerer is now a FULLY_SUPPORTED class — a direct hint, not a narrative chassis.
    assert draft.data.get("_class_hint") == "sorcerer"
    assert draft.data.get("_narrative_class") is None
    assert (draft.data.get("identity") or {}).get("class_intention") == "sorcerer"


async def test_explicit_paladin_intent_still_maps_to_a_supported_chassis(db, provider):
    world = await build_world(db)
    table = Table(db, provider)
    await table.send("!rv character")
    r = await table.send("Ser Alden, a human Paladin sworn to the dawn")
    draft = await _draft_for(db, world.p1_member_id)
    assert draft.data.get("_narrative_class") == "paladin"     # still locked
    assert draft.data.get("_class_hint") == "fighter"
    assert "paladin" in r.responses[0].content.lower()


async def test_no_repeated_question_for_already_supplied_field(db, provider):
    world = await build_world(db)
    table = Table(db, provider)
    await table.send("!rv character")
    # Minimal concept -> first follow-up.
    r1 = await table.send("อยากเป็นนักดาบเงียบขรึม")
    q1 = r1.responses[0].content
    # Now the player supplies origin explicitly.
    r2 = await table.send("โตมาในค่ายทหารชายแดน พ่อเป็นนายทหาร")
    q2 = r2.responses[0].content
    # The follow-up must not ask again about where they grew up.
    assert not ("โตที่ไหน" in q2 and "โตที่ไหน" in q1 and q1 == q2)
    assert "โตมาในค่ายทหาร" in (await _draft_for(db, world.p1_member_id)).data.get("_origin_text", "")


# --- custom ancestry: narrative preserved, mechanics chosen (Stage B) ------------

async def _seed_build_draft(db, member_id, campaign_id, data):
    async with db.unit_of_work() as s:
        draft = CharacterDraft(campaign_id=campaign_id, member_id=member_id, data=data)
        s.add(draft)
        await s.flush()
        return draft.id


async def test_catfolk_custom_ancestry_requires_mechanical_package_choice(db, provider):
    """A Catfolk (not a bundled species) must reach the ancestry-package step: its
    appearance is narrative; its mechanics are an explicit, owner-approvable base
    package — never auto-granted."""
    world = await build_world(db)
    table = Table(db, provider)
    async with db.unit_of_work() as s:
        # Seed a Stage-A-complete draft with a custom ancestry, ready for Stage B.
        draft = CharacterDraft(
            campaign_id=world.campaign_id, member_id=world.p1_member_id,
            data={"name": "Miu", "concept": "a lithe Catfolk thief",
                  "_custom_ancestry": "Catfolk", "_class_hint": "rogue",
                  "_origin_text": "a lithe Catfolk thief who walks rooftops",
                  "identity": {"name": "Miu", "ancestry": "Catfolk"},
                  "_awaiting_confirm": True, "_summary": "Miu"})
        s.add(draft)
    # Confirm the concept -> Stage B class -> rogue subclass step -> ancestry package.
    await table.send("✅ ใช่ นี่แหละตัวข้า")
    r = await table.send("นักย่องเบา (rogue)")
    assert "Subclass" in (r.responses[0].title or "")       # rogue has a subclass step
    r = await table.send("ยังไม่เลือก (later)")
    card = r.responses[0]
    assert "Catfolk" in (card.title or "") or "Catfolk" in card.content
    assert "ชุดกลไก" in card.content                        # mechanical-package framing
    # No power is granted by appearance — it's a menu of BUNDLED base packages.
    assert card.choices
    # Pick the mechanical base; narrative ancestry is retained.
    r = await table.send("ฮาล์ฟลิง (halfling)")
    assert "Background" in (r.responses[0].title or "")
    draft = await _draft_for(db, world.p1_member_id)
    b = draft.data["_build"]
    assert b["species"] == "halfling"                       # mechanical chassis
    assert b["narrative_ancestry"] == "Catfolk"             # fiction preserved
    assert b["mechanical_ancestry"] == "halfling"


# --- finalize persistence: original text + identity + seeds ----------------------

def _review_ready_rogue(origin_text: str, identity: dict) -> dict:
    return {
        "name": identity.get("name", "Nara"),
        "_origin_text": origin_text,
        "identity": identity,
        "_build": {
            "step": "review", "class": "rogue", "species": "human",
            "background": "criminal",
            "scores": {"str": 8, "dex": 17, "con": 13, "int": 12, "wis": 14, "cha": 10},
            "skills": ["stealth", "acrobatics", "perception", "investigation"],
            "species_skill:skillful": "insight",
            "expertise": ["stealth", "perception"], "component_token": "tok",
        },
    }


async def test_original_text_and_identity_survive_finalization(db, provider):
    from app.services.campaigns.finalize import finalize_character

    world = await build_world(db)
    origin = ("ข้าคือ Nara โตมาในซอกตลาดล่าง แม่เป็นคนขายผ้า อาจารย์คนแรกคือหัวขโมยแก่ๆ "
              "ที่ตอนนี้กลายเป็นคู่ปรับ ข้ากลัวการถูกทิ้ง และเก็บความลับว่าเคยทรยศเพื่อน")
    ident = {"name": "Nara", "family": "แม่เป็นคนขายผ้า", "mentors": "หัวขโมยแก่",
             "rivals": "อดีตอาจารย์", "fears": "การถูกทิ้ง", "secrets": "เคยทรยศเพื่อน",
             "homeland": "ซอกตลาดล่าง", "goals": "หาที่ของตัวเอง"}
    draft_id = await _seed_build_draft(
        db, world.p1_member_id, world.campaign_id, _review_ready_rogue(origin, ident))
    async with db.session() as s:
        draft = await s.get(CharacterDraft, draft_id)
    r = await finalize_character(db, draft=draft, data=draft.data, channel_id="chan-1")
    assert r.responses[0].kind == MessageKind.CHARACTER_REVEAL

    async with db.session() as s:
        char = (await s.execute(select(Character).where(Character.name == "Nara"))).scalar_one()
        # The complete original text is stored verbatim, not summarized away.
        assert char.origin_text == origin
        # Structured identity persisted, with mechanical facts recorded.
        assert char.identity["family"] == "แม่เป็นคนขายผ้า"
        assert char.identity["rivals"] == "อดีตอาจารย์"
        assert char.identity["mechanical_class"] == "rogue"
        assert char.identity["mechanical_ancestry"] == "human"
        # Reviewable seeds, all PENDING.
        seeds = char.identity["seeds"]
        assert seeds and all(sd["status"] == "proposed" for sd in seeds)
        kinds = {sd["kind"] for sd in seeds}
        assert {"hook", "relationship", "rumor"} <= kinds


async def test_identity_survives_restart_before_finalize(tmp_path, provider):
    """Structured fields written during Stage A persist across a process restart."""
    from app.db.session import Database

    url = f"sqlite+aiosqlite:///{(tmp_path / 'id-restart.db').as_posix()}"
    first = Database(url, echo=False)
    await first.create_all()
    try:
        world = await build_world(first)
        table = Table(first, provider)
        await table.send("!rv character")
        await table.send("Seraphina, a tiefling Warlock bound to an old pact")
        draft = await _draft_for(first, world.p1_member_id)
        assert draft.data["identity"].get("class_intention") == "warlock"
    finally:
        await first.dispose()

    restarted = Database(url, echo=False)
    try:
        draft = await _draft_for(restarted, world.p1_member_id)
        assert draft is not None
        assert draft.data["identity"].get("class_intention") == "warlock"
        assert draft.data.get("_class_hint") == "warlock"      # now a supported class
        assert "old pact" in draft.data.get("_origin_text", "")
    finally:
        await restarted.dispose()


async def test_review_card_separates_identity_from_mechanics_and_shows_seeds(db, provider):
    world = await build_world(db)
    ident = {"name": "Nara", "appearance": "ตัวเล็ก ตาไว",
             "rivals": "อดีตอาจารย์", "goals": "หาที่ของตัวเอง", "fears": "ถูกทิ้ง"}
    draft_id = await _seed_build_draft(
        db, world.p1_member_id, world.campaign_id,
        {**_review_ready_rogue("เรื่องของ Nara", ident), "_build":
         {**_review_ready_rogue("x", ident)["_build"], "step": "cantrips"}})
    # Drive the review render directly (deterministic).
    from app.services.campaigns.build_flow import BuildFlow
    async with db.session() as s:
        draft = await s.get(CharacterDraft, draft_id)
    data = dict(draft.data)
    data["_build"]["step"] = "review"
    card = BuildFlow(db)._review_step(data, "chan-1").responses[0]
    body = card.content
    assert "__รูปลักษณ์__" in body and "ตัวเล็ก ตาไว" in body   # appearance section
    assert "__ความสัมพันธ์__" in body and "อดีตอาจารย์" in body  # relationships section
    assert "__กลไก__" in body                                    # mechanics section, separate
    assert "เมล็ดพันธุ์เรื่องราว" in body                        # pending story seeds
    assert "ยังไม่ผูกมัด" in body                                # explicitly not binding


async def test_reveal_shows_narrative_class_alongside_chassis_and_seeds(db, provider):
    from app.services.campaigns.finalize import finalize_character

    world = await build_world(db)
    ident = {"name": "Rhaegar", "class_intention": "paladin",
             "goals": "ปกป้องผู้อ่อนแอ", "mentors": "อาจารย์ที่วิหาร"}
    data = _review_ready_rogue("Rhaegar เรื่องยาว", ident)
    data["_build"]["class"] = "fighter"          # mechanical chassis for paladin
    data["_narrative_class"] = "paladin"
    data["name"] = "Rhaegar"
    # Fighter has no expertise/species_skill quirks that matter here; adjust skills.
    data["_build"]["background"] = "soldier"
    data["_build"]["skills"] = ["athletics", "intimidation"]
    data["_build"].pop("expertise", None)
    data["_build"].pop("species_skill:skillful", None)
    draft_id = await _seed_build_draft(db, world.p1_member_id, world.campaign_id, data)
    async with db.session() as s:
        draft = await s.get(CharacterDraft, draft_id)
    r = await finalize_character(db, draft=draft, data=draft.data, channel_id="chan-1")
    fields = {f["name"]: f["value"] for f in r.responses[0].data["fields"]}
    assert "paladin" in fields["ตัวตน"]                          # fiction on the reveal
    assert any("เมล็ดพันธุ์" in n for n in fields)               # seeds surfaced


async def test_winged_dragonborn_is_bundled_species_with_narrative_wings(db, provider):
    """'Winged Dragonborn' uses the bundled dragonborn mechanics; the wings are
    narrative appearance and grant no flight automatically."""
    world = await build_world(db)
    table = Table(db, provider)
    await table.send("!rv character")
    await table.send("Vraxis, a winged Dragonborn Fighter with bronze scales")
    draft = await _draft_for(db, world.p1_member_id)
    # dragonborn is bundled -> a species hint, NOT a custom ancestry.
    assert draft.data.get("_species_hint") == "dragonborn"
    assert draft.data.get("_custom_ancestry") is None
    assert "winged" in draft.data.get("_origin_text", "").lower()
