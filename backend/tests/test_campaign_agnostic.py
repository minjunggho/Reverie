"""E7 — Reverie is campaign-agnostic (no universal tavern, canonical anchors,
AI-assisted campaign creation, authoritative wallets, visible time).

Pins the behaviors of the player-centered revamp:
- `!rv session start` with no world → setup-incomplete notice; NOTHING invented.
- `!rv campaign create <idea>` → reviewable proposal → owner approve → committed
  canon with AI_PROPOSED_CANON provenance and a canonical starting location.
- Rejected proposals commit nothing.
- Session ≥2 opens where the party IS (anchor), never back at the start.
- `!rv session start at <name>` — the owner picks explicitly.
- Wallets: starting funds, atomic spend, no accidental debt, idempotent retries.
- `!rv time` and owner-only `!rv diagnostics`.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import AdminBridge, InboundMessage, is_admin_command
from app.engine import build_bridge
from app.models.campaign import Campaign
from app.models.character import Character
from app.models.location import Location
from app.models.session import Session
from app.presentation import MessageKind
from tests.support.factories import build_world

_counter = {"n": 0}
OWNER, P1 = "u-owner", "u-p1"


def _msg(content, author, name="ผู้เล่น"):
    _counter["n"] += 1
    return InboundMessage(
        discord_message_id=f"ca{_counter['n']}", guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name=name, content=content,
    )


class Table:
    def __init__(self, db, provider, rng=None):
        self.game = build_bridge(db, provider=provider, rng=rng or SequenceRandomness(default=12))
        self.admin = AdminBridge(db, provider, creation_flow=self.game.creation_flow,
                                 session_zero=self.game.session_zero)

    async def send(self, content, author=OWNER, name="DM"):
        inbound = _msg(content, author, name)
        if is_admin_command(content):
            return await self.admin.handle(inbound)
        return await self.game.handle_inbound(inbound)


async def _fresh_table_with_character(db, provider) -> Table:
    table = Table(db, provider)
    await table.send("!rv campaign new โต๊ะทดสอบ")
    await table.send("!rv join", author=P1, name="กี้")
    await table.send("!rv character Kael rogue", author=P1, name="กี้")
    return table


async def _create_and_approve_world(table, premise="หมู่บ้านที่ชื่อผู้คนหายไปจากทะเบียน") -> str:
    r = await table.send(f"!rv campaign create {premise}")
    approve_id = re.search(r"approve (\w+)", r.responses[0].content).group(1)
    await table.send(f"!rv campaign import approve {approve_id}")
    return approve_id


# --- no universal fallback ------------------------------------------------------------

async def test_session_start_without_world_is_setup_notice_never_a_tavern(db, provider):
    table = await _fresh_table_with_character(db, provider)
    r = await table.send("!rv session start")
    assert r.responses[0].kind == MessageKind.TABLE_NOTICE
    assert "campaign create" in r.responses[0].content
    async with db.session() as s:
        assert (await s.execute(select(Location))).scalars().first() is None
        assert (await s.execute(select(Session))).scalars().first() is None


# --- AI campaign creation lifecycle ----------------------------------------------------

async def test_campaign_create_requires_owner_and_a_real_premise(db, provider):
    table = await _fresh_table_with_character(db, provider)
    r = await table.send("!rv campaign create เมืองท่าร้าง", author=P1, name="กี้")
    assert "เจ้าของโต๊ะ" in r.responses[0].content
    r = await table.send("!rv campaign create สั้นไป")
    assert "เล่าไอเดีย" in r.responses[0].content


async def test_campaign_create_approve_commits_ai_canon_and_starting_location(db, provider):
    table = await _fresh_table_with_character(db, provider)
    r = await table.send("!rv campaign create เมืองชายแดนที่ทะเบียนชื่อคนหายไป")
    body = r.responses[0].content
    assert "ยังไม่มีอะไรเป็น canon" in body          # review, not commit
    async with db.session() as s:
        assert (await s.execute(select(Location))).scalars().first() is None

    approve_id = re.search(r"approve (\w+)", body).group(1)
    await table.send(f"!rv campaign import approve {approve_id}")
    async with db.session() as s:
        locations = (await s.execute(select(Location))).scalars().all()
        assert {x.name for x in locations} == {"ลานเวรยามเก่า", "หอผู้ดูแลเขต", "แนวเขตเก่า"}
        assert all(x.provenance == "AI_PROPOSED_CANON" for x in locations)
        campaign = (await s.execute(select(Campaign))).scalar_one()
        start = next(x for x in locations if x.name == "ลานเวรยามเก่า")
        assert campaign.starting_location_id == start.id
        assert campaign.brief and campaign.central_question

    # Session 1 opens at the approved starting location, characters placed there.
    r = await table.send("!rv session start")
    assert r.responses[0].kind == MessageKind.SESSION_TITLE
    assert "ลานเวรยามเก่า" in r.responses[0].data["footer"]
    async with db.session() as s:
        campaign = (await s.execute(select(Campaign))).scalar_one()
        kael = (await s.execute(select(Character))).scalars().first()
        assert kael.location_id == campaign.starting_location_id
        assert campaign.current_party_anchor_id == campaign.starting_location_id


async def test_rejected_proposal_commits_nothing(db, provider):
    table = await _fresh_table_with_character(db, provider)
    r = await table.send("!rv campaign create อาณาจักรใต้ทะเลทรายที่ฝนไม่เคยตก")
    reject_id = re.search(r"reject (\w+)", r.responses[0].content).group(1)
    await table.send(f"!rv campaign import reject {reject_id}")
    async with db.session() as s:
        assert (await s.execute(select(Location))).scalars().first() is None
        campaign = (await s.execute(select(Campaign))).scalar_one()
        assert campaign.starting_location_id is None


# --- session continuity: open where the party IS ---------------------------------------

async def test_second_session_opens_at_party_anchor_not_the_start(db, provider):
    table = await _fresh_table_with_character(db, provider)
    await _create_and_approve_world(table)
    await table.send("!rv session start")
    await table.send("!rv session end")

    # The party moved during play (travel updates the anchor + positions).
    async with db.unit_of_work() as s:
        campaign = (await s.execute(select(Campaign))).scalar_one()
        hall = (await s.execute(select(Location).where(
            Location.name == "หอผู้ดูแลเขต"))).scalar_one()
        campaign.current_party_anchor_id = hall.id
        kael = (await s.execute(select(Character))).scalars().first()
        kael.location_id = hall.id

    r = await table.send("!rv session start")
    assert r.responses[0].kind == MessageKind.SESSION_TITLE
    assert "เซสชันที่ 2" in (r.responses[0].title or "")
    # Continuity: session 2 opens at the hall — session_prep must NOT teleport the
    # party back to the starting square.
    assert "หอผู้ดูแลเขต" in r.responses[0].data["footer"]


async def test_owner_can_pick_opening_location_by_name(db, provider):
    table = await _fresh_table_with_character(db, provider)
    await _create_and_approve_world(table)
    r = await table.send("!rv session start at แนวเขตเก่า")
    assert r.responses[0].kind == MessageKind.SESSION_TITLE
    assert "แนวเขตเก่า" in r.responses[0].data["footer"]
    r = await table.send("!rv session end")


async def test_single_location_campaign_opens_there_without_config(db, provider):
    """A factory world with exactly ONE location is unambiguous — it opens there
    (by count, never 'most recently created')."""
    world = await build_world(db)
    table = Table(db, provider)
    r = await table.send("!rv session start", author="owner-1", name="DM")
    assert r.responses[0].kind == MessageKind.SESSION_TITLE
    assert "โถงหน้าคฤหาสน์" in r.responses[0].data["footer"]


# --- wallet -----------------------------------------------------------------------------

async def test_quick_character_gets_starting_funds_and_wallet_view(db, provider):
    table = await _fresh_table_with_character(db, provider)
    r = await table.send("!rv wallet", author=P1, name="กี้")
    assert "เหรียญทอง" in r.responses[0].content            # rogue purse (10 gp)
    assert "ทุนเริ่มต้น" in r.responses[0].content           # ledger shows provenance


async def test_wallet_spend_is_atomic_and_never_accidentally_negative(db, provider):
    from app.core.errors import ConflictError, ValidationError
    from app.services.economy import WalletService

    world = await build_world(db)
    async with db.unit_of_work() as s:
        wallets = WalletService(s)
        await wallets.apply(character_id=world.kael_id, amounts={"gp": 10},
                            transaction_type="GRANT", reason="ทดสอบ")
    # Overspend refuses and changes nothing.
    try:
        async with db.unit_of_work() as s:
            await WalletService(s).apply(character_id=world.kael_id, amounts={"gp": -25},
                                         transaction_type="SPEND", reason="ของแพง")
        raise AssertionError("overspend must raise")
    except ValidationError:
        pass
    async with db.session() as s:
        assert (await WalletService(s).balance(world.kael_id)) == {"gp": 10}

    # Idempotent: a Discord retry with the same key commits at most once.
    async with db.unit_of_work() as s:
        await WalletService(s).apply(character_id=world.kael_id, amounts={"gp": -3},
                                     transaction_type="SPEND", reason="ค่าห้อง",
                                     idempotency_key="buy-room-1")
    try:
        async with db.unit_of_work() as s:
            await WalletService(s).apply(character_id=world.kael_id, amounts={"gp": -3},
                                         transaction_type="SPEND", reason="ค่าห้อง",
                                         idempotency_key="buy-room-1")
        raise AssertionError("duplicate idempotency key must raise")
    except ConflictError:
        pass
    async with db.session() as s:
        assert (await WalletService(s).balance(world.kael_id)) == {"gp": 7}


async def test_wallet_transfer_moves_money_between_characters(db, provider):
    from app.services.economy import WalletService

    world = await build_world(db)
    async with db.unit_of_work() as s:
        await WalletService(s).apply(character_id=world.kael_id, amounts={"gp": 8},
                                     transaction_type="GRANT", reason="ทดสอบ")
        await WalletService(s).transfer(from_character_id=world.kael_id,
                                        to_character_id=world.bront_id,
                                        amounts={"gp": 5}, reason="แบ่งค่าเสบียง")
    async with db.session() as s:
        assert (await WalletService(s).balance(world.kael_id)) == {"gp": 3}
        assert (await WalletService(s).balance(world.bront_id)) == {"gp": 5}


# --- time + diagnostics ------------------------------------------------------------------

async def test_time_command_reports_authoritative_clock_and_anchor(db, provider):
    table = await _fresh_table_with_character(db, provider)
    await _create_and_approve_world(table)
    await table.send("!rv session start")
    r = await table.send("!rv time", author=P1, name="กี้")
    assert "วันที่ 1" in r.responses[0].content
    assert "ลานเวรยามเก่า" in r.responses[0].content


async def test_diagnostics_is_owner_only_and_reports_versions_not_secrets(db, provider):
    table = await _fresh_table_with_character(db, provider)
    r = await table.send("!rv diagnostics", author=P1, name="กี้")
    assert "เจ้าของโต๊ะ" in r.responses[0].content
    r = await table.send("!rv diagnostics")
    body = r.responses[0].content
    assert "git:" in body and "llm:" in body and "world model:" in body
    assert "content `" in body                              # rules-content hash
    assert "sk-" not in body and "token" not in body.lower()  # never secrets
