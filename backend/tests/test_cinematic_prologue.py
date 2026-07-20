"""Regression tests for the v2 cinematic session-start scene.

The previous file tested the removed world→crisis→path multi-card prologue.  These
tests pin the player-facing replacement: one grounded scene, a bounded ScenePacket,
no secret leakage, and a real shared DecisionWindow.
"""
from __future__ import annotations

from sqlalchemy import select

from app.models.campaign import Campaign
from app.models.character import Character
from app.models.decision_window import DecisionWindow
from app.models.enums import Visibility
from app.models.knowledge import Secret
from app.models.location import Location
from app.models.world_graph import CampaignCanonRecord
from app.presentation import MessageKind
from app.schemas.llm_io import OpeningScene
from app.services.sessions import SessionOpeningService
from tests.support.factories import build_world

_MAIN_GOAL = "ตามหาและทำลายหัวใจของราชากลวงก่อนการฟื้นคืนชีพจะกลืนกินอาณาจักรเหนือ"


async def _seed_world_canon(db, world) -> None:
    async with db.unit_of_work() as s:
        campaign = await s.get(Campaign, world.campaign_id)
        campaign.central_question = _MAIN_GOAL
        campaign.brief = "ยุคที่เทพเงียบงันและอาณาจักรเก่ากำลังล่มสลาย"
        location = await s.get(Location, world.location_id)
        location.weather = "ลมหนาวและหมอกบาง"
        location.current_activity = "ยามกำลังลากกลอนประตูให้ปิดลง"
        kael = await s.get(Character, world.kael_id)
        kael.appearance = "ผ้าพันคอแดงซีดพันอยู่ที่ข้อมือ"
        kael.hooks = {"desire": "ตามหาน้องสาวที่หายไปในสงคราม"}
        s.add(CampaignCanonRecord(
            campaign_id=world.campaign_id,
            category="religion",
            fact="ศรัทธาเก่าเสื่อมถอย วิหารร้างผู้คน",
            visibility=Visibility.PUBLIC.value,
            importance=40,
        ))
        s.add(Secret(
            campaign_id=world.campaign_id,
            fact="SECRET_หัวใจซ่อนอยู่ใต้บัลลังก์ของราชาเมืองเอง",
            visibility=Visibility.DM_ONLY.value,
        ))


async def test_session_one_is_one_connected_scene_with_goal_and_window(db, provider):
    world = await build_world(db)
    await _seed_world_canon(db, world)
    captured = {}

    def _opening(messages, model):
        captured["blob"] = "\n".join(m["content"] for m in messages)
        return OpeningScene(
            title="กลอนประตูยามเที่ยงคืน",
            narration=(
                "ลมหนาวไล้ผ่านโถงหน้าคฤหาสน์ หมอกบางเกาะกระจกจนพร่ามัว\n\n"
                "ผ้าพันคอแดงซีดที่ข้อมือของ Kael ขยับตามลม ขณะที่ Bront ยืนอยู่ข้างกัน\n\n"
                "ยามกำลังลากกลอนประตูให้ปิดลง เสียงเหล็กครูดไม้ดังยาว "
                f"พวกคุณมาที่นี่เพื่อ{_MAIN_GOAL} และทางเข้ากำลังหายไปต่อหน้า"
            ),
            decision_prompt="ก่อนกลอนประตูจะลงสุด พวกคุณจะทำอย่างไร?",
            used_character_facts=["Kael.appearance", "Kael.desire"],
        )

    provider.on("generate_session_opening", _opening)
    result = await SessionOpeningService(db, provider).open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=[world.p1_member_id, world.p2_member_id],
        location_id=world.location_id,
    )

    assert len(result.messages) == 1
    message = result.messages[0]
    assert message.kind == MessageKind.SCENE_FRAME
    assert message.screen is not None
    assert message.data["connected_scene"] is True
    assert message.data["storytelling_pipeline_version"] == 2
    assert not any(m.kind == MessageKind.CAMPAIGN_PROLOGUE for m in result.messages)
    assert _MAIN_GOAL in message.content
    assert message.data["decision_prompt"].endswith("?")
    assert "SCENE_PACKET" in captured["blob"]
    assert "ลมหนาวและหมอกบาง" in captured["blob"]
    assert "ผ้าพันคอแดงซีดพันอยู่ที่ข้อมือ" in captured["blob"]

    async with db.session() as s:
        windows = list((await s.execute(select(DecisionWindow))).scalars())
    assert len(windows) == 1
    assert set(windows[0].required_actor_ids) == {world.kael_id, world.bront_id}


async def test_scene_packet_never_contains_dm_only_or_unknown_character_facts(db, provider):
    world = await build_world(db)
    await _seed_world_canon(db, world)
    captured = {}

    def _opening(messages, model):
        captured["user"] = "\n".join(
            m["content"] for m in messages if m["role"] == "user")
        return OpeningScene(
            title="เปิดฉาก",
            narration="พวกคุณยืนอยู่ในโถงหน้าคฤหาสน์ ขณะที่ยามกำลังปิดประตู",
            decision_prompt="พวกคุณจะทำอย่างไร?",
        )

    provider.on("generate_session_opening", _opening)
    await SessionOpeningService(db, provider).open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=[world.p1_member_id, world.p2_member_id],
        location_id=world.location_id,
    )
    blob = captured["user"]
    assert "SECRET_" not in blob
    assert "ผ้าพันคอแดงซีดพันอยู่ที่ข้อมือ" in blob
    # Bront has no stored appearance/backstory in the factory.  Empty fields are
    # omitted instead of inviting the narrator to fill them.
    bront_block = blob.split(f"'name': 'Bront'", 1)[1].split("\n- {", 1)[0]
    assert "'appearance':" not in bront_block
    assert "'relevant_facts':" not in bront_block
    assert "ช่องที่ไม่มีใน packet คือไม่ทราบ" in blob


async def test_parent_geography_is_context_not_a_prologue_card(db, provider):
    world = await build_world(db)
    async with db.unit_of_work() as s:
        region = Location(
            campaign_id=world.campaign_id,
            name="แคว้นเหนือ",
            location_type="REGION",
        )
        s.add(region)
        await s.flush()
        town = Location(
            campaign_id=world.campaign_id,
            name="เกรย์แมนเทิล",
            location_type="SETTLEMENT",
            parent_id=region.id,
        )
        s.add(town)
        await s.flush()
        opening = await s.get(Location, world.location_id)
        opening.parent_id = town.id

    captured = {}

    def _opening(messages, model):
        captured["blob"] = "\n".join(m["content"] for m in messages)
        return OpeningScene(
            title="เปิดฉาก",
            narration="พวกคุณอยู่ในโถงหน้าคฤหาสน์",
            decision_prompt="พวกคุณจะทำอย่างไร?",
        )

    provider.on("generate_session_opening", _opening)
    result = await SessionOpeningService(db, provider).open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=[world.p1_member_id, world.p2_member_id],
        location_id=world.location_id,
    )
    assert "parent_location: แคว้นเหนือ · เกรย์แมนเทิล" in captured["blob"]
    assert len(result.messages) == 1
    assert "เหตุการณ์ที่เปลี่ยนทุกอย่าง" not in result.messages[0].content
    assert "เส้นทางสู่พวกเจ้า" not in result.messages[0].content


async def test_sparse_campaign_still_opens_a_scene_not_synopsis_cards(db, provider):
    world = await build_world(db)
    result = await SessionOpeningService(db, provider).open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=[world.p1_member_id, world.p2_member_id],
        location_id=world.location_id,
    )
    assert len(result.messages) == 1
    message = result.messages[0]
    assert message.kind == MessageKind.SCENE_FRAME
    assert "โถงหน้าคฤหาสน์" in message.content
    assert "พวกคุณจะทำอย่างไร?" in message.content
    forbidden = ("Campaign description", "Important event", "Main objective",
                 "เหตุการณ์ที่เปลี่ยนทุกอย่าง", "เส้นทางสู่พวกเจ้า")
    assert not any(label in message.content for label in forbidden)
