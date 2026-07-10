"""Onboarding integration: the `!rv ...` setup commands create a real table, then a
committed `!` action plays through the game bridge end-to-end (fake provider).
Assertions target the presentation CONTRACT (kinds + structured data), not wording.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import AdminBridge, InboundMessage, is_admin_command
from app.engine import build_bridge
from app.models.enums import EventType
from app.models.event import Event
from app.presentation import MessageKind

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
        self.game = build_bridge(db, provider=provider, rng=rng)
        self.admin = AdminBridge(
            db, provider,
            creation_flow=self.game.creation_flow, session_zero=self.game.session_zero,
        )

    async def send(self, content, author="u-owner", name="DM"):
        inbound = _msg(content, author, name)
        if is_admin_command(content):
            return await self.admin.handle(inbound)
        return await self.game.handle_inbound(inbound)


async def test_full_onboarding_then_play(db, provider):
    table = Table(db, provider, SequenceRandomness([16]))  # Kael's stealth roll

    r = await table.send("!rv campaign new เงามนตรา", author="u-owner", name="DM")
    assert r.responses[0].kind == MessageKind.REVERIE_WELCOME
    assert "เงามนตรา" in (r.responses[0].title or "") + r.responses[0].content

    r = await table.send("!rv join", author="u-p1", name="กี้")
    assert r.responses[0].kind == MessageKind.TABLE_NOTICE
    r = await table.send("!rv character Kael rogue", author="u-p1", name="กี้")
    assert r.responses[0].kind == MessageKind.CHARACTER_REVEAL
    assert "Kael" in (r.responses[0].title or "")

    await table.send("!rv join", author="u-p2", name="โบ")
    await table.send("!rv character Bront fighter", author="u-p2", name="โบ")

    # Party view reflects the table.
    r = await table.send("!rv party", author="u-owner", name="DM")
    assert r.responses[0].kind == MessageKind.PARTY_STATUS
    names = " ".join(f.get("name", "") for f in r.responses[0].data["fields"])
    assert "Kael" in names and "Bront" in names

    # Only the owner can start; a player cannot.
    r = await table.send("!rv session start", author="u-p1", name="กี้")
    assert "เจ้าของโต๊ะ" in r.responses[0].content

    r = await table.send("!rv session start", author="u-owner", name="DM")
    kinds = [m.kind for m in r.responses]
    assert kinds[0] == MessageKind.SESSION_TITLE
    assert MessageKind.SCENE_FRAME in kinds
    assert "เซสชันที่ 1" in (r.responses[0].title or "")

    # Now a committed Thai action plays through the game bridge with a server roll.
    r = await table.send("! ผมค่อยๆ ย่องไปดูหน้าต่าง ไม่ให้ยามเห็น", author="u-p1", name="กี้")
    assert r.state_mutated and "outcome=success" in r.note
    assert r.responses[0].kind == MessageKind.CHECK_RESOLUTION
    assert "21" in r.responses[0].data["roll_line"]  # 16 + (3 DEX + 2 prof)

    async with db.session() as s:
        check = (
            await s.execute(
                select(Event).where(Event.event_type == EventType.ABILITY_CHECK_RESOLVED.value)
            )
        ).scalar_one()
        assert check.payload["skill"] == "stealth"
        assert check.mechanical_changes["natural_roll"] == 16   # the server die, not the LLM
        assert check.mechanical_changes["total"] == 21

    # Owner ends the session; closing beat + chronicle + light feedback ask.
    r = await table.send("!rv session end", author="u-owner", name="DM")
    kinds = [m.kind for m in r.responses]
    assert MessageKind.SCENE_TRANSITION in kinds        # closing beat
    assert MessageKind.SESSION_END in kinds             # chronicle
    assert kinds[-1] == MessageKind.TABLE_NOTICE        # feedback ask
    assert r.responses[-1].choices                      # one-tap options


async def test_admin_prefix_not_mistaken_for_committed_action():
    # `!rv ...` is an admin command; `!review ...` is a normal committed action.
    assert is_admin_command("!rv campaign new x") is True
    assert is_admin_command("!rv") is True
    assert is_admin_command("! ผมเปิดประตู") is False
    assert is_admin_command("!review ห้องนี้") is False  # not the admin prefix
