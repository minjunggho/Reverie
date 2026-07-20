"""The campaign's FIRST session opens with a grand, world-establishing cinematic
scene; later sessions use the tighter in-scene opener.

Both remain ONE connected scene (no synopsis cards), and the epic intro may name real
powers/faith ONLY from PUBLIC world canon — a DM-only secret never leaks, and an
offline fallback still establishes the world instead of a bare room.
"""
from __future__ import annotations

from app.core.errors import LLMError
from app.models.campaign import Campaign
from app.models.enums import Visibility
from app.models.knowledge import Secret
from app.models.world_graph import CampaignCanonRecord
from app.presentation import MessageKind
from app.schemas.llm_io import OpeningScene
from app.services.sessions import SessionOpeningService
from tests.support.factories import build_world

_GOAL = "ตามหาหัวใจของราชากลวงก่อนการฟื้นคืนจะกลืนแผ่นดินเหนือ"


async def _seed(db, world) -> None:
    async with db.unit_of_work() as s:
        c = await s.get(Campaign, world.campaign_id)
        c.brief = "ยุคที่เทพเงียบงันและอาณาจักรเก่ากำลังล่มสลาย"
        c.central_question = _GOAL
        s.add(CampaignCanonRecord(
            campaign_id=world.campaign_id, category="religion",
            fact="ศรัทธาเก่าเสื่อมถอย วิหารร้างผู้คน",
            visibility=Visibility.PUBLIC.value, importance=50))
        s.add(Secret(
            campaign_id=world.campaign_id,
            fact="SECRET_หัวใจซ่อนอยู่ใต้บัลลังก์ของราชาเมืองเอง",
            visibility=Visibility.DM_ONLY.value))


async def _open(db, provider, world):
    return await SessionOpeningService(db, provider).open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=[world.p1_member_id, world.p2_member_id],
        location_id=world.location_id,
    )


async def test_first_session_uses_grand_opener_with_public_world_canon(db, provider):
    world = await build_world(db)
    await _seed(db, world)
    cap = {}

    def _op(messages, model):
        cap["system"] = messages[0]["content"]
        cap["user"] = messages[1]["content"]
        return OpeningScene(
            title="เปิดเรื่อง",
            narration="พวกคุณยืนอยู่ในโถงหน้าคฤหาสน์ ขณะที่โลกเก่ากำลังสั่นคลอน",
            decision_prompt="พวกคุณจะทำอย่างไร?")

    provider.on("generate_session_opening", _op)
    result = await _open(db, provider, world)

    # The grand, world-establishing opener was selected for session 1.
    assert "เซสชันแรกของทั้งแคมเปญ" in cap["system"]
    # PUBLIC world canon reached the packet; a DM-only secret did not.
    assert "world_canon" in cap["user"]
    assert "ศรัทธาเก่าเสื่อมถอย" in cap["user"]
    assert "SECRET_" not in cap["user"]
    # Still ONE connected scene — never the removed multi-card prologue.
    assert len(result.messages) == 1
    msg = result.messages[0]
    assert msg.kind == MessageKind.SCENE_FRAME
    assert msg.data["connected_scene"] is True
    assert not any(m.kind == MessageKind.CAMPAIGN_PROLOGUE for m in result.messages)


async def test_later_session_uses_tight_opener_not_the_grand_one(db, provider):
    world = await build_world(db)
    await _seed(db, world)
    systems: list[str] = []

    def _op(messages, model):
        systems.append(messages[0]["content"])
        return OpeningScene(
            title="เปิดฉาก", narration="พวกคุณอยู่ในโถงหน้าคฤหาสน์",
            decision_prompt="พวกคุณจะทำอย่างไร?")

    provider.on("generate_session_opening", _op)
    await _open(db, provider, world)  # session 1
    await _open(db, provider, world)  # session 2

    assert "เซสชันแรกของทั้งแคมเปญ" in systems[0]        # session 1 = grand
    assert "เซสชันแรกของทั้งแคมเปญ" not in systems[1]    # session 2 = tight
    assert "ไม่ใช่ผู้ประกาศสรุปแคมเปญ" in systems[1]     # the OPENING_SYSTEM signature


async def test_first_session_offline_fallback_establishes_the_world(db, provider):
    world = await build_world(db)
    await _seed(db, world)

    def _fail(messages, model):
        raise LLMError("offline")

    provider.on("generate_session_opening", _fail)
    result = await _open(db, provider, world)

    assert len(result.messages) == 1
    msg = result.messages[0]
    assert msg.kind == MessageKind.SCENE_FRAME
    # Even offline, the world is established from the brief + PUBLIC canon.
    assert "ยุคที่เทพเงียบงัน" in msg.content
    assert "ศรัทธาเก่าเสื่อมถอย" in msg.content
    assert "SECRET_" not in msg.content
    assert "พวกคุณจะทำอย่างไร?" in msg.content
    forbidden = ("Campaign description", "Main objective",
                 "เหตุการณ์ที่เปลี่ยนทุกอย่าง", "เส้นทางสู่พวกเจ้า")
    assert not any(f in msg.content for f in forbidden)
