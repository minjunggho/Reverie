"""A session opening must reach the channel even if the native Components V2 send
fails — the "session start showed nothing" failure.

The bridge returns a valid SCENE_FRAME (proven elsewhere); the risk is purely in
delivery: on a gateway/library build where a V2 LayoutView send raises, the opening
must degrade to plain text + buttons rather than vanish. A plain-text notice already
renders, which is why 'มีเซสชันที่กำลังเล่นอยู่แล้ว' shows but the scene did not.
"""
from __future__ import annotations

import discord

from app.discord_bridge.dto import OutboundMessage
from app.presentation import MessageKind
from app.presentation.screens import cinematic_scene_screen
from discord_bot.client import ReverieClient


class _Recorder:
    """A stand-in Discord channel. Optionally fails native V2 (LayoutView) sends the
    way an unsupported gateway build / component-limit rejection would."""

    def __init__(self, *, fail_views: bool) -> None:
        self.fail_views = fail_views
        self.view_sends = 0
        self.text_sends: list[str] = []

    async def send(self, content=None, *, view=None, embed=None):
        if isinstance(view, discord.ui.LayoutView):
            self.view_sends += 1
            if self.fail_views:
                raise RuntimeError("components v2 not supported on this build")
            return
        self.text_sends.append(content or "")


def _opening_message() -> OutboundMessage:
    screen = cinematic_scene_screen(
        metadata="| กลางดึก | 00:00 น. | วันที่ 1 | ทางเข้าดันเจียน | เย็นและมืด |",
        narration="ลมเย็นจากป่าพัดลอดผ่านซากเสาหินตรงทางเข้า กลิ่นดินเปียกคละคลุ้ง",
        decision_prompt="พวกคุณจะทำอย่างไร?",
        planning_window_id="win-1",
        planning_status=["○ Kael — รอการกระทำ"],
    )
    return OutboundMessage(
        "chan-1", screen.to_text(), kind=MessageKind.SCENE_FRAME, screen=screen)


async def test_v2_failure_falls_back_to_text_so_opening_never_vanishes():
    client = ReverieClient(object(), object())
    channel = _Recorder(fail_views=True)

    await client._send_one(channel, _opening_message())

    assert channel.view_sends == 1                 # the native V2 send was attempted
    assert channel.text_sends                       # ...and it still went out as text
    assert "พวกคุณจะทำอย่างไร?" in "\n".join(channel.text_sends)


async def test_v2_success_sends_a_single_layout_view():
    client = ReverieClient(object(), object())
    channel = _Recorder(fail_views=False)

    await client._send_one(channel, _opening_message())

    assert channel.view_sends == 1
    assert channel.text_sends == []                 # no redundant text when V2 works
