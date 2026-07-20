"""Cinematic-style guarantees for the v2 session narrator.

The Realm reference is used as a behavioural rubric, never a prose snapshot: these
tests assert the *shape* the reference taught us — the world already in motion, each
character placed inside the scene with one grounded detail woven into an action, no
generic campaign cards, and a clear closing decision — plus that the deterministic
offline fallback keeps those guarantees instead of degrading into a status list.
"""
from __future__ import annotations

from app.ai.prompts.system_prompts import NARRATOR_SYSTEM_EXTRA, OPENING_SYSTEM
from app.ai.prompts.thai_dm_style import THAI_DM_STYLE
from app.ai.prompts.thai_narration_templates import THAI_NARRATION_TEMPLATES
from app.core.errors import LLMError
from app.presentation import MessageKind
from tests.support.factories import build_world
from tests.test_storytelling_pipeline_v2 import (
    _enrich_opening_state,
    _start_by_command,
)

_CARD_LABELS = (
    "Main objective", "Campaign description", "Important event", "Path toward",
    "NPC instruction", "เหตุการณ์ที่เปลี่ยนทุกอย่าง", "เส้นทางสู่พวกเจ้า",
    "เป้าหมายหลัก:",
)


def _fail(messages, model):
    raise LLMError("offline")


async def test_fallback_weaves_each_character_detail_into_a_connected_scene(db, provider):
    """Offline, the opening still reads as a scene: the world is mid-action, each PC
    is placed on its own beat with one grounded detail woven into an active line, and
    it closes on a decision — never a "name — stat; stat" dump or a campaign card."""
    world = await build_world(db)
    objective = await _enrich_opening_state(db, world)  # gives Kael an appearance + hook
    provider.on("generate_session_opening", _fail)

    out = (await _start_by_command(db, provider)).responses[0]
    content = out.content
    assert out.kind == MessageKind.SCENE_FRAME

    # World already doing something + the establishing place description are present.
    assert "โถงกว้าง มีหน้าต่างบานใหญ่ทางทิศตะวันตก และประตูไม้เก่า" in content
    assert "ยามเฝ้าประตูกำลังตรวจกลอนเหล็กทีละบาน" in content

    # Each character gets its own placement beat (camera resting on each in turn).
    assert "ยืนอยู่ตรงนี้" in content      # first character
    assert "ยืนอยู่ข้าง" in content        # subsequent character(s)

    # Kael's stored appearance is woven into HIS line, not listed elsewhere.
    kael_line = next(
        line for line in content.splitlines() if line.startswith("Kael ")
    )
    assert "ผ้าคลุมสีหม่นและแผลเป็นเล็กเหนือคิ้วซ้าย" in kael_line

    # Objective is dramatized into prose and the scene closes on a real decision.
    assert objective in content
    assert "พวกคุณจะทำอย่างไร?" in content

    # Regression guard: never the old flat "Name อยู่ที่<place> — a; b; c" list,
    # and never a generic campaign-briefing card.
    assert "อยู่ที่โถงหน้าคฤหาสน์ —" not in content
    assert not any(label in content for label in _CARD_LABELS)


async def test_fallback_never_narrates_the_placeholder_scene_purpose(db, provider):
    """A campaign with no explicit intent must not have the scaffolding purpose
    ("เปิดฉากและส่งการตัดสินใจให้ปาร์ตี้") leak into player-facing prose."""
    world = await build_world(db)  # no enrichment -> purpose is the placeholder
    provider.on("generate_session_opening", _fail)

    out = (await _start_by_command(db, provider)).responses[0]
    assert out.kind == MessageKind.SCENE_FRAME
    assert "เปิดฉากและส่งการตัดสินใจให้ปาร์ตี้" not in out.content
    assert "พวกคุณจะทำอย่างไร?" in out.content
    assert not any(label in out.content for label in _CARD_LABELS)


def test_opening_system_prompt_teaches_cinematic_techniques():
    # Per-character camera weaving, a world already in motion, image-sequence pacing.
    assert "เอ่ยชื่อตัวละครแล้วถัก" in OPENING_SYSTEM
    assert "โลกต้องกำลังเคลื่อนไหวอยู่แล้ว" in OPENING_SYSTEM
    assert "ลำดับภาพ" in OPENING_SYSTEM
    # Mechanical authority still lives with the engine.
    assert "ห้ามบอกว่าโดน พลาด" in OPENING_SYSTEM


def test_dm_style_expands_intent_and_preserves_player_monologue_and_casting():
    assert "ขยายการกระทำให้เป็นฉาก" in THAI_DM_STYLE
    assert "คำพูดจริงของผู้เล่น" in THAI_DM_STYLE
    assert "การร่ายคาถา" in THAI_DM_STYLE


def test_templates_include_casting_and_player_monologue_beats():
    for key in ("casting", "player_monologue"):
        assert key in THAI_NARRATION_TEMPLATES
        assert THAI_NARRATION_TEMPLATES[key].strip()


def test_narrator_may_voice_an_invoked_utterance_but_only_grounded_in_canon():
    # The narrator MAY speak the cry/command/prayer the player's action invokes...
    assert "เปล่งถ้อยคำนั้นแทนตัวละครได้" in THAI_DM_STYLE
    assert "เปล่งถ้อยคำนั้นแทน ACTOR ได้" in NARRATOR_SYSTEM_EXTRA
    # ...invoking a deity ONLY by its canonical name, never a new decision.
    assert "เฉพาะชื่อที่อยู่ใน CHARACTER_CONTEXT" in THAI_DM_STYLE
    assert "ห้ามเติมการตัดสินใจ" in THAI_DM_STYLE
    # The opening (no submitted action yet) still never invents player speech.
    assert "ห้ามประกาศอารมณ์ ความคิด หรือบทพูดของตัวละครผู้เล่นเอง" in OPENING_SYSTEM
