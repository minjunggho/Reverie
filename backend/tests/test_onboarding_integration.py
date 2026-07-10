"""Onboarding integration: the `!rv ...` setup commands create a real table, then a
committed `!` action plays through the game bridge end-to-end (fake provider)."""
from __future__ import annotations

from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import AdminBridge, InboundMessage, is_admin_command
from app.engine import build_bridge
from app.models.enums import EventType
from app.models.event import Event

_counter = {"n": 0}


def _msg(content, author, name):
    _counter["n"] += 1
    return InboundMessage(
        discord_message_id=f"m{_counter['n']}", guild_id="guild-1", channel_id="chan-1",
        author_discord_id=author, author_display_name=name, content=content,
    )


class Table:
    """Routes a message to the admin or game bridge exactly like the bot does."""

    def __init__(self, db, provider, rng):
        self.admin = AdminBridge(db, provider)
        self.game = build_bridge(db, provider=provider, rng=rng)

    async def send(self, content, author="u-owner", name="DM"):
        inbound = _msg(content, author, name)
        if is_admin_command(content):
            return await self.admin.handle(inbound)
        return await self.game.handle_inbound(inbound)


async def test_full_onboarding_then_play(db, provider):
    table = Table(db, provider, SequenceRandomness([16]))  # Kael's stealth roll

    r = await table.send("!rv campaign new เงามนตรา", author="u-owner", name="DM")
    assert "สร้างแคมเปญ" in r.responses[0].content

    r = await table.send("!rv join", author="u-p1", name="กี้")
    assert "เข้าร่วม" in r.responses[0].content
    r = await table.send("!rv character Kael rogue", author="u-p1", name="กี้")
    assert "Kael" in r.responses[0].content and "rogue" in r.responses[0].content

    await table.send("!rv join", author="u-p2", name="โบ")
    await table.send("!rv character Bront fighter", author="u-p2", name="โบ")

    # Status reflects the table.
    r = await table.send("!rv status", author="u-owner", name="DM")
    assert "Kael" in r.responses[0].content and "Bront" in r.responses[0].content

    # Only the owner can start; a player cannot.
    r = await table.send("!rv session start", author="u-p1", name="กี้")
    assert "เจ้าของโต๊ะ" in r.responses[0].content

    r = await table.send("!rv session start", author="u-owner", name="DM")
    assert "เซสชันที่ 1 เริ่มแล้ว" in r.responses[0].content

    # Now a committed Thai action plays through the game bridge with a server roll.
    r = await table.send("! ผมค่อยๆ ย่องไปดูหน้าต่าง ไม่ให้ยามเห็น", author="u-p1", name="กี้")
    assert r.state_mutated and "outcome=success" in r.note

    async with db.session() as s:
        check = (
            await s.execute(
                select(Event).where(Event.event_type == EventType.ABILITY_CHECK_RESOLVED.value)
            )
        ).scalar_one()
        assert check.payload["skill"] == "stealth"
        assert check.mechanical_changes["natural_roll"] == 16   # the server die, not the LLM
        assert check.mechanical_changes["total"] == 21          # 16 + (3 DEX + 2 prof)

    # Owner ends the session; a player-safe summary comes back.
    r = await table.send("!rv session end", author="u-owner", name="DM")
    assert "จบเซสชัน" in r.responses[0].content


async def test_admin_prefix_not_mistaken_for_committed_action():
    # `!rv ...` is an admin command; `!review ...` is a normal committed action.
    assert is_admin_command("!rv campaign new x") is True
    assert is_admin_command("!rv") is True
    assert is_admin_command("! ผมเปิดประตู") is False
    assert is_admin_command("!review ห้องนี้") is False  # not the admin prefix
