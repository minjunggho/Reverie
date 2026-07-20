"""Golden-rubric tests derived from the supplied Realm gameplay reference.

They intentionally avoid exact prose snapshots.  The rubric checks grounding,
scene continuity, Thai-first structure, mechanics authority, shared planning, and
Discord cohesion—the useful behaviours of the reference rather than its wording or
rules mistakes.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.ai.prompts.system_prompts import OPENING_SYSTEM, ROUND_NARRATOR_SYSTEM
from app.ai.prompts.thai_narration_templates import THAI_NARRATION_TEMPLATES
from app.core.errors import LLMError
from app.core.randomness import SequenceRandomness
from app.discord_bridge import AdminBridge, InboundMessage
from app.engine import build_bridge
from app.models.campaign import Campaign, CampaignMember
from app.models.character import Character
from app.models.decision_window import ActionSubmission, DecisionWindow
from app.models.enums import WindowPhase
from app.models.location import Location
from app.models.progression import ActiveEffect
from app.models.scene import Scene
from app.models.session import Session
from app.presentation import MessageKind
from app.schemas.llm_io import OpeningScene
from tests.support.factories import build_world

_seq = 0


def _message(content: str, *, author: str, name: str = "ผู้เล่น") -> InboundMessage:
    global _seq
    _seq += 1
    return InboundMessage(
        discord_message_id=f"story-v2-{_seq}",
        guild_id="guild-1",
        channel_id="chan-1",
        author_discord_id=author,
        author_display_name=name,
        content=content,
    )


async def _start_by_command(db, provider):
    return await AdminBridge(db, provider).handle(
        _message("!rv session start", author="owner-1", name="DM"))


async def _enrich_opening_state(db, world) -> str:
    objective = "นำบัญชีรายชื่อออกจากคฤหาสน์ก่อนยามเปลี่ยนเวร"
    async with db.unit_of_work() as s:
        campaign = await s.get(Campaign, world.campaign_id)
        campaign.current_game_time = 23 * 60 + 47
        campaign.brief = "คฤหาสน์ชายแดนเป็นจุดส่งของผิดกฎหมาย"
        campaign.central_question = objective
        campaign.main_story = {
            "state": "opening",
            "goals": [{"key": "main", "text": objective, "status": "open"}],
            "deadlines": [{"what": "ยามเปลี่ยนเวร", "at_minute": 24 * 60}],
            "leads": [],
        }
        location = await s.get(Location, world.location_id)
        location.weather = "ลมเย็นและฝนละเอียด"
        location.current_activity = "ยามเฝ้าประตูกำลังตรวจกลอนเหล็กทีละบาน"
        location.contents = ["ลังไม้ประทับตรา", "ประตูไม้เก่า"]
        location.state = {
            "lighting": "แสงตะเกียงสั่นไหว",
            "conditions": ["พื้นหินเปียกลื่น"],
            "hazards": ["หน้าต่างฝั่งตะวันตกมองเห็นโถงทั้งหมด"],
        }
        kael = await s.get(Character, world.kael_id)
        kael.appearance = "ผ้าคลุมสีหม่นและแผลเป็นเล็กเหนือคิ้วซ้าย"
        kael.hooks = {"desire": "ล้างชื่อครอบครัวที่ถูกใส่ร้าย"}
    return objective


async def test_real_command_uses_v2_scene_packet_and_one_connected_discord_screen(db, provider):
    world = await build_world(db)
    objective = await _enrich_opening_state(db, world)
    captured = {}

    def _opening(messages, model):
        captured["system"] = messages[0]["content"]
        captured["user"] = messages[1]["content"]
        return OpeningScene(
            title="กลอนเหล็กก่อนเที่ยงคืน",
            narration=(
                "ฝนละเอียดเคาะหน้าต่างโถงหน้าคฤหาสน์เป็นจังหวะ ลมเย็นลอดรอยไม้ "
                "พื้นหินเปียกส่งกลิ่นชื้นใต้แสงตะเกียงที่สั่นไหว\n\n"
                "Kael ยืนอยู่กับ Bront ผ้าคลุมสีหม่นแนบไหล่ "
                "แผลเป็นเล็กเหนือคิ้วซ้ายรับแสงวาบเมื่อเขาหันไปทางประตู\n\n"
                "ยามเฝ้าประตูกำลังตรวจกลอนเหล็กทีละบาน "
                f"บัญชีที่พวกคุณต้อง{objective}ยังอยู่ลึกเข้าไป และเหลือเวลาไม่มาก"
            ),
            decision_prompt="ก่อนกลอนบานสุดท้ายจะปิด พวกคุณจะทำอย่างไร?",
            used_character_facts=["Kael.appearance", "Kael.desire"],
        )

    provider.on("generate_session_opening", _opening)
    result = await _start_by_command(db, provider)

    assert len(result.responses) == 1
    out = result.responses[0]
    assert out.kind == MessageKind.SCENE_FRAME
    assert out.screen is not None
    assert out.data["storytelling_pipeline_version"] == 2
    assert out.data["connected_scene"] is True
    assert out.data["location"] == "โถงหน้าคฤหาสน์"
    assert "| ดึก | 23:47 น. | วันที่ 1 |" in out.content
    assert objective in out.content
    assert out.data["decision_prompt"].endswith("?")
    assert "SCENE_PACKET" in captured["user"]
    assert "ลมเย็นและฝนละเอียด" in captured["user"]
    assert "ลังไม้ประทับตรา" in captured["user"]
    assert "ผลทอย" in captured["system"]  # explicit mechanical-authority prohibition
    assert not any(label in out.content for label in (
        "Campaign description", "Important event", "Path toward", "Main objective",
        "เหตุการณ์ที่เปลี่ยนทุกอย่าง", "เส้นทางสู่พวกเจ้า",
    ))


async def test_fallback_is_grounded_thai_scene_and_never_generic_cards(db, provider):
    world = await build_world(db)
    objective = await _enrich_opening_state(db, world)

    def _fail(messages, model):
        raise LLMError("offline")

    provider.on("generate_session_opening", _fail)
    result = await _start_by_command(db, provider)
    out = result.responses[0]
    assert out.kind == MessageKind.SCENE_FRAME
    assert len(result.responses) == 1
    assert "โถงหน้าคฤหาสน์" in out.content
    assert "ยามเฝ้าประตูกำลังตรวจกลอนเหล็กทีละบาน" in out.content
    assert objective in out.content
    assert "พวกคุณจะทำอย่างไร?" in out.content
    assert "Main objective" not in out.content
    assert "Campaign description" not in out.content


async def test_two_player_opening_collects_edits_and_waits_for_everyone_ready(db, provider):
    world = await build_world(db)
    await _enrich_opening_state(db, world)
    start = await _start_by_command(db, provider)
    window_id = start.responses[0].data["decision_window_id"]
    assert window_id
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))

    first = await bridge.handle_inbound(_message(
        "! Kael ย่องไปตรวจกลอนประตู",
        author=world.p1_discord_id,
        name="กี้",
    ))
    assert first.responses[0].kind == MessageKind.TABLE_NOTICE
    assert not any(m.kind == MessageKind.SCENE_FRAME for m in first.responses)

    await bridge.handle_inbound(_message(
        f"~rv-ready:{window_id}", author=world.p1_discord_id, name="กี้"))
    edit = await bridge.handle_inbound(_message(
        "! Kael เปลี่ยนไปตรวจลังไม้ก่อน",
        author=world.p1_discord_id,
        name="กี้",
    ))
    assert "ยังแก้ได้" in edit.responses[0].content
    async with db.session() as s:
        sub = (await s.execute(select(ActionSubmission).where(
            ActionSubmission.window_id == window_id,
            ActionSubmission.actor_id == world.kael_id,
        ))).scalar_one()
        assert sub.revision == 2
        assert not sub.is_ready

    await bridge.handle_inbound(_message(
        "! Bront ยืนบังสายตายามให้ Kael",
        author=world.p2_discord_id,
        name="โบ",
    ))
    await bridge.handle_inbound(_message(
        f"~rv-ready:{window_id}", author=world.p2_discord_id, name="โบ"))
    # Bront is ready, but Kael's edit cleared Ready: still no resolution.
    async with db.session() as s:
        window = await s.get(DecisionWindow, window_id)
        assert window.resolved is False

    resolved = await bridge.handle_inbound(_message(
        f"~rv-ready:{window_id}", author=world.p1_discord_id, name="กี้"))
    story = [m for m in resolved.responses if m.kind == MessageKind.SCENE_FRAME]
    assert len(story) == 1
    assert story[0].data["connected_scene"] is True
    async with db.session() as s:
        old = await s.get(DecisionWindow, window_id)
        next_windows = list((await s.execute(select(DecisionWindow).where(
            DecisionWindow.scene_id == old.scene_id,
            DecisionWindow.round_id == 2,
        ))).scalars())
    assert old.resolved is True
    assert len(next_windows) == 1


async def test_configured_solo_window_auto_readies_and_resolves_at_pipeline_depth(db, provider):
    """Single-player uses the SAME window/ready system, but the one action resolves
    through the FULL committed pipeline — a real verified check, not the shared
    resolver's coordination-only intent recording — then the slot is consumed and the
    next window opens."""
    world = await build_world(db)
    async with db.unit_of_work() as s:
        campaign = await s.get(Campaign, world.campaign_id)
        campaign.config = {
            **(campaign.config or {}),
            "dice_mode": "AUTO",     # resolve the check in one step for a clean assertion
            "planning": {
                "enabled": "always",
                "single_player_auto_ready": True,
                "manual_ready_solo": False,
            },
        }
        # Only Kael attends the command-created session.
        bront_owner = await s.get(CampaignMember, world.p2_member_id)
        bront_owner.active_character_id = None

    start = await _start_by_command(db, provider)
    window_id = start.responses[0].data["decision_window_id"]
    assert window_id
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    resolved = await bridge.handle_inbound(_message(
        "! Kael ตรวจประตูไม้เก่า",
        author=world.p1_discord_id,
        name="กี้",
    ))
    # The full pipeline resolved it (a verified check), not the shallow shared resolver.
    assert any(m.kind == MessageKind.CHECK_RESOLUTION for m in resolved.responses)
    assert "outcome=" in (resolved.note or "")
    async with db.session() as s:
        window = await s.get(DecisionWindow, window_id)
        sub = (await s.execute(select(ActionSubmission).where(
            ActionSubmission.window_id == window_id,
        ))).scalar_one()
        # This planning slot was consumed and the next window opened.
        next_windows = list((await s.execute(select(DecisionWindow).where(
            DecisionWindow.scene_id == window.scene_id,
            DecisionWindow.round_id == 2,
        ))).scalars())
    assert sub.is_ready
    assert window.resolved is True
    assert len(next_windows) == 1


async def test_solo_opening_is_a_clean_cinematic_scene_without_a_planning_panel(db, provider):
    """A single-player table gets the clean cinematic intro (narration + one decision),
    NOT a shared planning panel — solo has no one to coordinate with."""
    world = await build_world(db)
    await _enrich_opening_state(db, world)
    async with db.unit_of_work() as s:
        bront = await s.get(CampaignMember, world.p2_member_id)
        bront.active_character_id = None      # only Kael attends

    start = await _start_by_command(db, provider)
    assert len(start.responses) == 1
    out = start.responses[0]
    assert out.kind == MessageKind.SCENE_FRAME          # the cinematic opening still fires
    assert out.data.get("decision_window_id") is None   # no solo planning window by default
    assert out.data["connected_scene"] is True


async def test_host_force_resolve_ends_the_round_without_everyone_ready(db, provider):
    world = await build_world(db)
    await _enrich_opening_state(db, world)
    start = await _start_by_command(db, provider)
    window_id = start.responses[0].data["decision_window_id"]
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    # Only Kael submits; Bront never readies.
    await bridge.handle_inbound(_message(
        "! Kael ย่องไปตรวจกลอนประตู", author=world.p1_discord_id, name="กี้"))
    forced = await bridge.handle_inbound(_message(
        f"~rv-force:{window_id}", author="owner-1", name="DM"))
    assert any(m.kind == MessageKind.SCENE_FRAME for m in forced.responses)
    async with db.session() as s:
        assert (await s.get(DecisionWindow, window_id)).resolved is True


async def test_a_player_cannot_use_host_only_controls(db, provider):
    world = await build_world(db)
    await _enrich_opening_state(db, world)
    start = await _start_by_command(db, provider)
    window_id = start.responses[0].data["decision_window_id"]
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    r = await bridge.handle_inbound(_message(
        f"~rv-force:{window_id}", author=world.p1_discord_id, name="กี้"))
    assert "ผู้ดูแล" in r.responses[0].content
    async with db.session() as s:
        assert (await s.get(DecisionWindow, window_id)).resolved is False


async def test_host_reopen_returns_to_planning_and_clears_ready(db, provider):
    world = await build_world(db)
    await _enrich_opening_state(db, world)
    start = await _start_by_command(db, provider)
    window_id = start.responses[0].data["decision_window_id"]
    bridge = build_bridge(db, provider=provider, rng=SequenceRandomness(default=10))
    await bridge.handle_inbound(_message(
        "! Kael ย่องไปตรวจกลอนประตู", author=world.p1_discord_id, name="กี้"))
    await bridge.handle_inbound(_message(
        f"~rv-ready:{window_id}", author=world.p1_discord_id, name="กี้"))
    await bridge.handle_inbound(_message(
        f"~rv-reopen:{window_id}", author="owner-1", name="DM"))
    async with db.session() as s:
        window = await s.get(DecisionWindow, window_id)
        sub = (await s.execute(select(ActionSubmission).where(
            ActionSubmission.window_id == window_id,
            ActionSubmission.actor_id == world.kael_id))).scalar_one()
    assert window.phase == WindowPhase.AWAITING_ACTIONS.value
    assert window.resolved is False
    assert not sub.is_ready                   # reopen cleared readiness


async def test_restart_does_not_duplicate_active_opening_and_migrates_config(db, provider):
    world = await build_world(db)
    first = await _start_by_command(db, provider)
    second = await _start_by_command(db, provider)
    assert first.responses[0].kind == MessageKind.SCENE_FRAME
    assert "กำลังเล่นอยู่แล้ว" in second.responses[0].content
    async with db.session() as s:
        session_count = (await s.execute(select(func.count(Session.id)))).scalar_one()
        scene_count = (await s.execute(select(func.count(Scene.id)))).scalar_one()
        campaign = await s.get(Campaign, world.campaign_id)
    assert session_count == 1
    assert scene_count == 1
    assert campaign.config["storytelling_pipeline_version"] == 2
    assert campaign.config["opening_cinematic_played"] is True


async def test_later_session_packet_preserves_injury_condition_effect_object_and_place(db, provider):
    world = await build_world(db)
    await _start_by_command(db, provider)
    await AdminBridge(db, provider).handle(
        _message("!rv session end", author="owner-1", name="DM"))
    captured = {}
    async with db.unit_of_work() as s:
        kael = await s.get(Character, world.kael_id)
        kael.hp = 3
        kael.conditions = ["poisoned"]
        location = await s.get(Location, world.location_id)
        location.contents = ["กุญแจทองแดงที่ Kael เก็บไว้"]
        location.current_activity = "น้ำกำลังไหลเข้ามาตามรอยแตกใต้ประตู"
        s.add(ActiveEffect(
            campaign_id=world.campaign_id,
            character_id=world.kael_id,
            name="คำอวยพร",
            active=True,
            targets=[f"character:{world.kael_id}"],
        ))

    def _opening(messages, model):
        captured["packet"] = messages[1]["content"]
        return OpeningScene(
            title="น้ำใต้ประตู",
            narration=(
                "Kael ยังมีบาดแผลและคำอวยพรค้างอยู่ "
                "น้ำกำลังไหลเข้ามาตามรอยแตกใต้ประตูใกล้กุญแจทองแดง"
            ),
            decision_prompt="พวกคุณจะรับมืออย่างไร?",
        )

    provider.on("generate_session_opening", _opening)
    await _start_by_command(db, provider)
    packet = captured["packet"]
    assert "บาดเจ็บ (HP 3/9)" in packet
    assert "poisoned" in packet
    assert "คำอวยพร" in packet
    assert "กุญแจทองแดงที่ Kael เก็บไว้" in packet
    assert "น้ำกำลังไหลเข้ามา" in packet


def test_thai_native_templates_cover_required_story_and_mechanical_beats():
    required = {
        "session_opening", "exploration", "social_interaction", "investigation",
        "shared_action_resolution", "combat_beginning", "initiative_request",
        "attack_roll_request", "saving_throw_request", "hit", "miss",
        "critical_hit", "damage", "condition_applied", "round_summary",
        "combat_ending", "immediate_aftermath", "major_discovery",
        "failed_check_with_complication",
    }
    assert required <= THAI_NARRATION_TEMPLATES.keys()
    assert all(THAI_NARRATION_TEMPLATES[key].strip() for key in required)
    assert "ห้ามบอกว่าโดน พลาด" in OPENING_SYSTEM
    assert "intent_recorded" in ROUND_NARRATOR_SYSTEM
    assert "ห้ามบอกว่าสำเร็จ/ล้มเหลว" in ROUND_NARRATOR_SYSTEM
