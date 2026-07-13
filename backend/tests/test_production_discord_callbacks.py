"""Release-verification: drive the REAL production discord.py wiring.

`discord_bot/client.py` is entirely `# pragma: no cover` — no test in the suite
had ever executed `ReverieClient._view_for`, its `on_choice` closure, or
`_send_one`/`_deliver` before this file. Every prior character-creation and
follow/unfollow test went through `AdminBridge`/`DiscordBridge` directly and a
synthetic `on_choice` (see `test_discord_adapter_keeps_paginated_components_
inside_platform_limits`), which never exercises interaction acknowledgement,
identity extraction from `interaction.user`, or delivery.

This is NOT a live Discord gateway test — there is no bot token, no real guild,
no real human clicking anything, and that limitation is real: Discord's actual
interaction-ack timing, rate limits, and gateway reconnect behavior cannot be
observed here. What this DOES verify: the actual `discord.py` `View`/`Button`/
`Select` objects the bot constructs, wired through the actual `ReverieClient`
callback closures, against a duck-typed `Interaction` double that satisfies
every attribute the production code touches (`response.defer()`, `id`,
`guild_id`, `user.id`, `user.display_name`, `channel`). A defect in component
routing, double-defer, identity leakage, or delivery-after-defer would surface
here; it could not surface in a bridge-only test.
"""
from __future__ import annotations

import asyncio

import discord
import pytest
from sqlalchemy import select

from app.db.session import Database
from app.discord_bridge import AdminBridge, is_admin_command
from app.discord_bridge.dto import InboundMessage
from app.engine import build_bridge
from app.models.character import Character
from app.models.character_draft import CharacterDraft
from app.presentation import MessageKind
from app.rules_content import get_registry
from discord_bot.client import ReverieClient
from tests.support.factories import build_world
from tests.test_character_creation_recovery import _complete_build_data

pytestmark = pytest.mark.asyncio


# --- production-shaped Discord doubles (no network) -----------------------------

class FakeResponse:
    def __init__(self) -> None:
        self.defer_calls = 0

    async def defer(self) -> None:
        self.defer_calls += 1


class FakeChannel:
    def __init__(self, channel_id: str = "chan-1") -> None:
        self.id = channel_id
        self.sent: list[dict] = []

    async def send(self, content: str | None = None, *, embed=None, view=None):
        self.sent.append({"content": content, "embed": embed, "view": view})
        return _FakeMessage()


class _FakeMessage:
    id = 999


class FakeUser:
    """Production code only ever does `str(interaction.user.id)` — so the double
    must round-trip to the SAME id string that was used to join the campaign, not
    a derived hash. Real Discord ids are numeric snowflakes; test fixtures use
    readable strings like "disc-p1", so `.id` here is left as the given value
    rather than forced through `int()`, matching what `str(...)` must produce."""

    def __init__(self, user_id: str, display_name: str) -> None:
        self.id = user_id
        self.display_name = display_name


class FakeInteraction:
    """Duck-types exactly what `client.py`'s `on_choice` touches."""

    _counter = 0

    def __init__(self, *, user_id: str, display_name: str, channel: FakeChannel,
                guild_id: str = "guild-1") -> None:
        FakeInteraction._counter += 1
        self.id = 1_000_000 + FakeInteraction._counter
        self.guild_id = guild_id
        self.user = FakeUser(user_id, display_name)
        self.channel = channel
        self.response = FakeResponse()


def _msg(content, author="disc-owner", name="DM", channel_id="chan-1", guild="guild-1"):
    return InboundMessage(
        discord_message_id=f"seed-{author}-{content[:12]}", guild_id=guild,
        channel_id=channel_id, author_discord_id=author, author_display_name=name,
        content=content,
    )


class LiveTable:
    """A `ReverieClient` over an in-memory bridge/admin pair — the production
    routing (`_route`) and delivery (`_deliver`/`_send_one`) code, unconnected
    to any gateway."""

    def __init__(self, db, provider) -> None:
        self.game = build_bridge(db, provider=provider)
        self.admin = AdminBridge(
            db, provider, creation_flow=self.game.creation_flow,
            session_zero=self.game.session_zero,
        )
        self.client = ReverieClient(bridge=self.game, admin=self.admin)
        self.channel = FakeChannel()

    async def send_text(self, content, author="disc-owner", name="DM"):
        """The `on_message` path: build an inbound, route, deliver — real code.
        Returns (sent_dict, out) where `out` is the actual `OutboundMessage` the
        engine produced (so a caller can feed it straight into `.click()`, exactly
        as `client.py` would from the SAME object it just rendered)."""
        result = await self.client._route(_msg(content, author=author, name=name))
        await self.client._deliver(self.channel, result)
        sent = self.channel.sent[-1] if self.channel.sent else None
        out = result.responses[-1] if result.responses else None
        return sent, out

    async def click(self, out, *, choice_label: str, user_id: str, display_name: str):
        """Find the REAL button/select whose value matches `choice_label` inside
        the View `client.py` actually constructed for `out`, and invoke its REAL
        `.callback`, exactly as discord.py would on a live click."""
        view = self.client._view_for(out)
        assert view is not None, "no interactive components on this message"
        interaction = FakeInteraction(user_id=user_id, display_name=display_name,
                                      channel=self.channel)
        target = self._find_control(view, choice_label)
        await target.callback(interaction)
        return interaction

    @staticmethod
    def _find_control(view, label_or_value: str):
        for child in view.children:
            if isinstance(child, discord.ui.Button) and (
                child.label == label_or_value or label_or_value in (child.label or "")
            ):
                return child
        for child in view.children:
            if isinstance(child, discord.ui.Select):
                for option in child.options:
                    if option.value == label_or_value or option.label == label_or_value:
                        child._values = [option.value]
                        return _SelectInvoker(child)
        raise AssertionError(f"no control matches {label_or_value!r}")


class _SelectInvoker:
    """discord.py's Select.callback reads `select.values`, which pulls from
    `_values` — set by discord.py after a real user picks an option."""

    def __init__(self, select: discord.ui.Select) -> None:
        self._select = select

    async def callback(self, interaction):
        await self._select.callback(interaction)


async def _full_build_data(campaign_id, member_id, *, class_name="rogue") -> dict:
    """A build ready to confirm at the review step, matching a real playthrough."""
    presets = {
        "rogue": dict(species="human", background="criminal",
                     scores={"str": 8, "dex": 17, "con": 13, "int": 12, "wis": 14, "cha": 10},
                     skills=["stealth", "acrobatics", "perception", "investigation"],
                     expertise=["stealth", "perception"], extra={}),
        "wizard": dict(species="human", background="sage",
                       scores={"str": 8, "dex": 14, "con": 13, "int": 17, "wis": 12, "cha": 10},
                       skills=["arcana", "investigation"],
                       expertise=[], extra={"cantrips": ["fire_bolt", "mage_hand", "light"],
                                            "book": ["magic_missile", "shield", "mage_armor",
                                                    "detect_magic", "identify", "sleep"],
                                            "prepared": ["magic_missile", "shield", "mage_armor",
                                                        "detect_magic"]}),
        "cleric": dict(species="human", background="acolyte",
                       scores={"str": 14, "dex": 10, "con": 13, "int": 8, "wis": 17, "cha": 12},
                       skills=["religion", "medicine"],
                       expertise=[], extra={"cantrips": ["sacred_flame", "guidance", "thaumaturgy"],
                                            "prepared": ["cure_wounds", "bless", "guiding_bolt",
                                                        "shield_of_faith"]}),
    }
    p = presets[class_name]
    build = {
        "step": "review", "class": class_name, "species": p["species"],
        "background": p["background"], "scores": p["scores"], "skills": p["skills"],
        "expertise": p["expertise"], "component_token": "prod-tok",
    }
    build.update(p["extra"])
    if class_name == "human" or p["species"] == "human":
        build["species_skill:skillful"] = "insight"
    return {"name": f"Test{class_name.title()}", "_build": build}


async def _new_characters_named(db, name: str) -> list[Character]:
    """`build_world` already gives p1/p2 an active character (Kael/Bront), so
    counting a member's TOTAL characters after finalize would always be off by
    one. Finalize-created test characters use distinct `Test<Class>` names —
    filter on that instead of coupling to fixture-character counts."""
    async with db.session() as s:
        return list((await s.execute(
            select(Character).where(Character.name == name))).scalars())


async def _seed_draft(db, campaign_id, member_id, class_name) -> str:
    async with db.unit_of_work() as s:
        draft = CharacterDraft(
            campaign_id=campaign_id, member_id=member_id,
            data=await _full_build_data(campaign_id, member_id, class_name=class_name),
        )
        s.add(draft)
        await s.flush()
        return draft.id


# --- diagnostics: real embed, no secrets ----------------------------------------

async def test_diagnostics_renders_as_real_embed_without_secrets(db, provider):
    table = LiveTable(db, provider)
    await table.send_text("!rv campaign new โต๊ะสด")
    sent, _out = await table.send_text("!rv diagnostics")
    assert sent["embed"] is not None
    body = sent["embed"].description or ""
    assert "git:" in body and "llm:" in body
    assert "sk-" not in body and "token" not in body.lower() and "key" not in body.lower()


# --- final confirmation: the reported failure, reproduced through the real click path --

@pytest.mark.parametrize("class_name", ["rogue", "wizard", "cleric"])
async def test_confirm_button_click_creates_character_exactly_once(db, provider, class_name):
    world = await build_world(db)
    draft_id = await _seed_draft(db, world.campaign_id, world.p1_member_id, class_name)
    table = LiveTable(db, provider)

    sent, out = await table.send_text("!rv resume", author=world.p1_discord_id, name="กี้")
    assert sent["embed"] is not None and "ตรวจทานครั้งสุดท้าย" in (sent["embed"].title or "")

    interaction = await table.click(
        out, choice_label="✅ สร้างเลย",
        user_id=world.p1_discord_id, display_name="กี้",
    )
    # The interaction was acknowledged — this is the difference between a real
    # "This interaction failed" and a clean click. defer() must fire exactly once.
    assert interaction.response.defer_calls == 1

    reveal = table.channel.sent[-1]
    assert reveal["embed"] is not None
    assert "🎭" in (reveal["embed"].title or "")

    created = await _new_characters_named(db, f"Test{class_name.title()}")
    assert len(created) == 1
    async with db.session() as s:
        draft = await s.get(CharacterDraft, draft_id)
        assert draft.status == "DONE"          # closed only AFTER the commit

    sheet, _ = await table.send_text("!rv sheet", author=world.p1_discord_id, name="กี้")
    assert sheet["embed"] is not None
    inv, _ = await table.send_text("!rv inventory", author=world.p1_discord_id, name="กี้")
    assert inv["embed"] is not None
    wal, _ = await table.send_text("!rv wallet", author=world.p1_discord_id, name="กี้")
    assert wal["embed"] is not None

    resumed, _ = await table.send_text("!rv resume", author=world.p1_discord_id, name="กี้")
    assert "ยังไม่มีตัวละครที่สร้างค้างไว้" in (resumed["embed"].description or resumed["content"] or "")


async def test_rapid_double_click_creates_exactly_one_character(db, provider):
    """The exact reported failure mode: the player double-taps the confirm
    button. Two REAL, DISTINCT interaction events (as Discord would actually
    deliver) fire concurrently through the real callback."""
    world = await build_world(db)
    await _seed_draft(db, world.campaign_id, world.p1_member_id, "rogue")
    table = LiveTable(db, provider)
    _sent, out = await table.send_text("!rv resume", author=world.p1_discord_id, name="กี้")
    view1 = table.client._view_for(out)
    view2 = table.client._view_for(out)   # a second, independent View for the 2nd tap
    btn1 = table._find_control(view1, "✅ สร้างเลย")
    btn2 = table._find_control(view2, "✅ สร้างเลย")
    i1 = FakeInteraction(user_id=world.p1_discord_id, display_name="กี้", channel=table.channel)
    i2 = FakeInteraction(user_id=world.p1_discord_id, display_name="กี้", channel=table.channel)

    await asyncio.gather(btn1.callback(i1), btn2.callback(i2))

    assert i1.response.defer_calls == 1 and i2.response.defer_calls == 1  # both acked
    created = await _new_characters_named(db, "TestRogue")
    assert len(created) == 1, "double-click must never create two characters"


async def test_two_players_finalize_concurrently_without_interference(db, provider):
    world = await build_world(db)
    d1 = await _seed_draft(db, world.campaign_id, world.p1_member_id, "rogue")
    d2 = await _seed_draft(db, world.campaign_id, world.p2_member_id, "wizard")
    table = LiveTable(db, provider)

    _s1, out1 = await table.send_text("!rv resume", author=world.p1_discord_id, name="กี้")
    _s2, out2 = await table.send_text("!rv resume", author=world.p2_discord_id, name="ไหม")
    v1 = table.client._view_for(out1)
    v2 = table.client._view_for(out2)
    btn1 = table._find_control(v1, "✅ สร้างเลย")
    btn2 = table._find_control(v2, "✅ สร้างเลย")
    i1 = FakeInteraction(user_id=world.p1_discord_id, display_name="กี้", channel=table.channel)
    i2 = FakeInteraction(user_id=world.p2_discord_id, display_name="ไหม", channel=table.channel)

    await asyncio.gather(btn1.callback(i1), btn2.callback(i2))

    c1 = await _new_characters_named(db, "TestRogue")
    c2 = await _new_characters_named(db, "TestWizard")
    assert len(c1) == 1 and len(c2) == 1
    async with db.session() as s:
        d1_row, d2_row = await s.get(CharacterDraft, d1), await s.get(CharacterDraft, d2)
        assert d1_row.status == "DONE" and d2_row.status == "DONE"


async def test_foreign_player_clicking_confirm_never_finalizes_someone_elses_draft(db, provider):
    """Identity comes from `interaction.user.id`, never from message content or
    who is holding the phone — this is the actual security property client.py
    must uphold: clicking someone else's button acts as YOU, on YOUR draft."""
    world = await build_world(db)
    await _seed_draft(db, world.campaign_id, world.p1_member_id, "rogue")
    table = LiveTable(db, provider)
    _sent, out = await table.send_text("!rv resume", author=world.p1_discord_id, name="กี้")
    view = table.client._view_for(out)
    btn = table._find_control(view, "✅ สร้างเลย")

    # P2 clicks the button rendered on P1's message.
    interaction = FakeInteraction(user_id=world.p2_discord_id, display_name="ไหม",
                                  channel=table.channel)
    await btn.callback(interaction)

    # P2's click on P1's message did not create P1's character (nor a phantom
    # character for P2, who has no draft at all).
    assert await _new_characters_named(db, "TestRogue") == []
    async with db.session() as s:
        row = (await s.execute(select(CharacterDraft).where(
            CharacterDraft.member_id == world.p1_member_id))).scalars().first()
        assert row.status == "ACTIVE"   # P1's draft untouched by P2's click


async def _seed_spell_step_draft(db, world, *, selected=None) -> None:
    data = _complete_build_data("cantrips", class_name="wizard",
                                selected=selected or ["fire_bolt"])
    async with db.unit_of_work() as s:
        s.add(CharacterDraft(campaign_id=world.campaign_id,
                             member_id=world.p1_member_id, data=data))


async def test_stale_pre_restart_control_rejected_after_bot_restart(tmp_path, provider):
    """`component_token` is deliberately PERSISTED, not regenerated on restart —
    a pending click that arrives right after a restart must still work, so a
    naive "does the token change across restart" test finds nothing (verified:
    it doesn't, by design). The REAL staleness the code defends against is a
    control captured on one STEP being replayed after the draft has moved past
    that step — e.g. the player finishes cantrips, the bot restarts, and a
    leftover cantrips-step select (rendered before the advance) still arrives.
    That must be rejected without touching the NEW step's selections."""
    path = (tmp_path / "stale-control.db").as_posix()
    url = f"sqlite+aiosqlite:///{path}"
    first_db = Database(url, echo=False)
    await first_db.create_all()
    try:
        world = await build_world(first_db)
        # Cantrips already complete (3/3) — one real confirm click away from book.
        cls = get_registry().get_class("wizard")
        cantrip_pool = [s.name for s in get_registry().spells_for_class("wizard", 0)]
        await _seed_spell_step_draft(
            first_db, world, selected=cantrip_pool[:cls.spellcasting.cantrips_known])
        table = LiveTable(first_db, provider)
        _sent, out = await table.send_text("!rv resume", author=world.p1_discord_id, name="กี้")
        stale_view = table.client._view_for(out)
        # The leftover control: a cantrips-step SELECT, captured before advancing.
        stale_select_src = next(c for c in stale_view.children if isinstance(c, discord.ui.Select)
                                and c.custom_id.startswith("rv-spell-pick"))
        stale_pick_value = stale_select_src.options[0].value

        # Advance for real: confirm cantrips -> the draft's step becomes "book".
        confirm = next(c for c in stale_view.children if isinstance(c, discord.ui.Button)
                       and c.label and "ยืนยันตัวเลือก" in c.label)
        assert not confirm.disabled
        interaction = FakeInteraction(user_id=world.p1_discord_id, display_name="กี้",
                                      channel=table.channel)
        await confirm.callback(interaction)
        async with first_db.session() as s:
            row = (await s.execute(select(CharacterDraft).where(
                CharacterDraft.member_id == world.p1_member_id))).scalars().first()
            assert row.data["_build"]["step"] == "book"
    finally:
        await first_db.dispose()

    restarted_db = Database(url, echo=False)
    try:
        table2 = LiveTable(restarted_db, provider)
        async with restarted_db.session() as s:
            before = (await s.execute(select(CharacterDraft).where(
                CharacterDraft.member_id == world.p1_member_id))).scalars().first()
            book_before = list((before.data.get("_build") or {}).get("book") or [])
        resumed, out = await table2.send_text(
            "!rv resume", author=world.p1_discord_id, name="กี้")
        assert "ตำรา" in (resumed["embed"].title or "")   # now on the BOOK step

        # Replay the leftover CANTRIPS-step control against the current view.
        # discord.py routes by the CLICKED item, so simulate via a fresh Select
        # sharing the current view's custom_id but carrying the stale VALUE.
        current_view = table2.client._view_for(out)
        current_select = next(c for c in current_view.children if isinstance(c, discord.ui.Select)
                              and c.custom_id.startswith("rv-spell-pick"))
        interaction = FakeInteraction(user_id=world.p1_discord_id, display_name="กี้",
                                      channel=table2.channel)
        current_select._values = [stale_pick_value]     # stale step embedded in the value
        await current_select.callback(interaction)
        assert interaction.response.defer_calls == 1      # still acked, no failure

        notice = table2.channel.sent[-1]
        text = (notice["embed"].description if notice["embed"] else notice["content"]) or ""
        assert "ขั้นตอนปัจจุบัน" in text or "หน้าก่อน" in text   # the stale-control notice

        async with restarted_db.session() as s:
            draft = (await s.execute(select(CharacterDraft).where(
                CharacterDraft.member_id == world.p1_member_id))).scalars().first()
            assert draft.status == "ACTIVE"
            assert draft.data["_build"]["step"] == "book"       # unmoved by the replay
            # The stale replay changed NOTHING on the current (book) step either.
            assert (draft.data.get("_build") or {}).get("book") == book_before

        # A FRESH control (this process's own current-step token+step) still
        # works normally right after the rejected stale replay.
        fresh_pick = next(o for o in current_select.options if o.value != stale_pick_value)
        current_select._values = [fresh_pick.value]
        interaction2 = FakeInteraction(user_id=world.p1_discord_id, display_name="กี้",
                                       channel=table2.channel)
        await current_select.callback(interaction2)
        async with restarted_db.session() as s:
            draft = (await s.execute(select(CharacterDraft).where(
                CharacterDraft.member_id == world.p1_member_id))).scalars().first()
            assert len((draft.data.get("_build") or {}).get("book") or []) == \
                len(book_before) + 1
    finally:
        await restarted_db.dispose()


# --- restart resume mid-spell-selection ------------------------------------------

async def test_resume_after_restart_returns_exact_step_and_selections(tmp_path, provider):
    path = (tmp_path / "resume-restart.db").as_posix()
    url = f"sqlite+aiosqlite:///{path}"
    first_db = Database(url, echo=False)
    await first_db.create_all()
    try:
        world = await build_world(first_db)
        await _seed_spell_step_draft(first_db, world, selected=["fire_bolt"])
        table = LiveTable(first_db, provider)
        sent, _out = await table.send_text("!rv resume", author=world.p1_discord_id, name="กี้")
        assert "คาถา" in (sent["embed"].title or "")
        assert "1 / " in (sent["embed"].description or "")   # 1 already selected
    finally:
        await first_db.dispose()

    restarted_db = Database(url, echo=False)
    try:
        table2 = LiveTable(restarted_db, provider)
        resumed, out = await table2.send_text(
            "!rv resume", author=world.p1_discord_id, name="กี้")
        # Exact step AND exact prior selection survive the restart.
        assert "คาถา" in (resumed["embed"].title or "")
        assert "1 / " in (resumed["embed"].description or "")
        assert "ลูกไฟพุ่ง" in (resumed["embed"].description or "")   # fire_bolt's Thai hint

        # A real select click after restart correctly ADDS to the surviving
        # selection rather than replacing/losing it.
        view = table2.client._view_for(out)
        pick_menu = next(c for c in view.children if isinstance(c, discord.ui.Select)
                         and c.custom_id.startswith("rv-spell-pick"))
        new_pick = next(o for o in pick_menu.options if "fire_bolt" not in o.value)
        pick_menu._values = [new_pick.value]
        interaction = FakeInteraction(user_id=world.p1_discord_id, display_name="กี้",
                                      channel=table2.channel)
        await pick_menu.callback(interaction)
        assert interaction.response.defer_calls == 1
        async with restarted_db.session() as s:
            draft = (await s.execute(select(CharacterDraft).where(
                CharacterDraft.member_id == world.p1_member_id))).scalars().first()
            cantrips = (draft.data.get("_build") or {}).get("cantrips") or []
            assert "fire_bolt" in cantrips and len(cantrips) == 2
    finally:
        await restarted_db.dispose()


async def test_finalize_after_bot_restart_creates_character_once(tmp_path, provider):
    """The review-ready draft survives a restart, and confirming AFTER restart
    (through the real click path, on a freshly constructed process/View) creates
    the character exactly once."""
    path = (tmp_path / "finalize-restart.db").as_posix()
    url = f"sqlite+aiosqlite:///{path}"
    first_db = Database(url, echo=False)
    await first_db.create_all()
    try:
        world = await build_world(first_db)
        await _seed_draft(first_db, world.campaign_id, world.p1_member_id, "cleric")
    finally:
        await first_db.dispose()

    restarted_db = Database(url, echo=False)
    try:
        table = LiveTable(restarted_db, provider)
        resumed, out = await table.send_text(
            "!rv resume", author=world.p1_discord_id, name="กี้")
        assert "ตรวจทานครั้งสุดท้าย" in (resumed["embed"].title or "")

        interaction = await table.click(out, choice_label="✅ สร้างเลย",
                                        user_id=world.p1_discord_id, display_name="กี้")
        assert interaction.response.defer_calls == 1
        created = await _new_characters_named(restarted_db, "TestCleric")
        assert len(created) == 1
    finally:
        await restarted_db.dispose()


# --- on_message: the ONE piece of client.py logic `_route`-based tests skip -----
# (bot-filtering and the attachment size gate live ONLY in on_message itself)

class FakeAttachment:
    def __init__(self, *, filename: str, size: int, content_type: str = "text/markdown",
                data: bytes = b"content") -> None:
        self.filename = filename
        self.size = size
        self.content_type = content_type
        self._data = data

    async def read(self) -> bytes:
        return self._data


class FakeAuthor:
    def __init__(self, *, user_id: str, display_name: str, bot: bool = False) -> None:
        self.id = user_id
        self.display_name = display_name
        self.bot = bot


class FakeGuild:
    id = "guild-1"


class FakeMessage:
    def __init__(self, *, content: str, author: FakeAuthor, channel: FakeChannel,
                attachments: list | None = None, guild=FakeGuild()) -> None:
        self.id = "msg-1"
        self.content = content
        self.author = author
        self.channel = channel
        self.guild = guild
        self.attachments = attachments or []


async def test_on_message_ignores_messages_from_bots(db, provider):
    """The ONLY early-return in `on_message` that isn't exercised anywhere else —
    a bot's own message (or another bot's) must never be routed at all."""
    table = LiveTable(db, provider)
    channel = FakeChannel()
    bot_author = FakeAuthor(user_id="disc-p1", display_name="กี้", bot=True)
    message = FakeMessage(content="!rv diagnostics", author=bot_author, channel=channel)
    await table.client.on_message(message)
    assert channel.sent == []          # never routed, never delivered


async def test_on_message_rejects_oversized_attachment_before_reading_it(db, provider):
    """Files over 1MB get a plain rejection and the campaign-import route is never
    reached — the real gate `discord_bot/client.py` uses before AdminBridge sees
    any attachment bytes."""
    table = LiveTable(db, provider)
    await table.send_text("!rv campaign new โต๊ะสด", author="disc-owner", name="DM")
    channel = FakeChannel()
    author = FakeAuthor(user_id="disc-owner", display_name="DM")
    big = FakeAttachment(filename="world.md", size=2_000_000)
    message = FakeMessage(content="!rv campaign import", author=author, channel=channel,
                          attachments=[big])
    await table.client.on_message(message)
    assert len(channel.sent) == 1
    assert "1 MB" in (channel.sent[0]["content"] or "")


async def test_on_message_routes_a_real_text_command_end_to_end(db, provider):
    """The full `on_message` path — NOT `_route` directly — for a plain command,
    proving guild/channel/author extraction from the fake Message produces a
    working `InboundMessage` the bridge accepts."""
    table = LiveTable(db, provider)
    channel = FakeChannel()
    author = FakeAuthor(user_id="disc-new-owner", display_name="DM")
    message = FakeMessage(content="!rv campaign new จากช่องสด", author=author, channel=channel)
    await table.client.on_message(message)
    assert len(channel.sent) == 1
    assert channel.sent[0]["embed"] is not None or channel.sent[0]["content"]
