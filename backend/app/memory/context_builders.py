"""Context builders — the only place the engine assembles LLM inputs.

Each returns a list of `LLMMessage`. Player-facing builders read only through
visibility-filtered queries, so restricted content never enters the message list.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMMessage
from app.ai.prompts.system_prompts import (
    ADJUDICATOR_SYSTEM,
    CLASSIFIER_SYSTEM,
    CONSEQUENCE_SYSTEM,
    INTERPRETER_SYSTEM,
    NARRATOR_SYSTEM_EXTRA,
    NPC_RESPONSE_SYSTEM,
    RECAP_SYSTEM,
)
from app.ai.prompts.thai_dm_style import THAI_DM_STYLE
from app.models.character import Character
from app.models.enums import Visibility
from app.models.location import Location
from app.models.scene import Scene
from app.services.events import EventService


@dataclass
class SceneBrief:
    mode: str
    purpose: str
    location_name: str
    visible_entities: list[str]

    def as_text(self) -> str:
        return (
            f"โหมด={self.mode}; สถานที่={self.location_name}; "
            f"เป้าหมายฉาก={self.purpose}; "
            f"สิ่งที่เห็น={', '.join(self.visible_entities) or '-'}"
        )


async def scene_brief(session: AsyncSession, scene: Scene | None) -> SceneBrief:
    location_name = "-"
    if scene is not None and scene.location_id:
        loc = await session.get(Location, scene.location_id)
        if loc is not None:
            location_name = loc.name
    if scene is None:
        return SceneBrief("-", "-", location_name, [])
    return SceneBrief(
        mode=scene.mode,
        purpose=scene.purpose or "-",
        location_name=location_name,
        visible_entities=list(scene.visible_entity_ids or []),
    )


def _character_capabilities(character: Character | None) -> str:
    if character is None:
        return "-"
    return (
        f"{character.name} (lvl {character.level} {character.char_class}); "
        f"skills={', '.join(character.proficiencies) or '-'}; "
        f"hp={character.hp}/{character.max_hp}; ac={character.ac}"
    )


# --- Phase 5: classification -------------------------------------------------
async def build_classification_context(
    session: AsyncSession, *, message_text: str, scene: Scene | None
) -> list[LLMMessage]:
    brief = await scene_brief(session, scene)
    return [
        {"role": "system", "content": CLASSIFIER_SYSTEM},
        {"role": "user", "content": f"SCENE: {brief.as_text()}\nMESSAGE: {message_text}"},
    ]


# --- Phase 6: interpretation -------------------------------------------------
async def build_action_interpretation_context(
    session: AsyncSession, *, action_text: str, scene: Scene | None, character: Character | None
) -> list[LLMMessage]:
    brief = await scene_brief(session, scene)
    return [
        {"role": "system", "content": INTERPRETER_SYSTEM},
        {
            "role": "user",
            "content": (
                f"SCENE: {brief.as_text()}\n"
                f"CHARACTER: {_character_capabilities(character)}\n"
                f"ACTION: {action_text}"
            ),
        },
    ]


# --- Phase 7: adjudication ---------------------------------------------------
async def build_adjudication_context(
    session: AsyncSession, *, action_text: str, interpretation_summary: str,
    scene: Scene | None, character: Character | None,
) -> list[LLMMessage]:
    brief = await scene_brief(session, scene)
    return [
        {"role": "system", "content": ADJUDICATOR_SYSTEM},
        {
            "role": "user",
            "content": (
                f"SCENE: {brief.as_text()}\n"
                f"CHARACTER: {_character_capabilities(character)}\n"
                f"INTERPRETATION: {interpretation_summary}\n"
                f"ACTION: {action_text}"
            ),
        },
    ]


# --- Phase 7/8: consequence planning -----------------------------------------
async def build_consequence_context(
    session: AsyncSession, *, action_text: str, outcome: str, scene: Scene | None,
    target_ref: str | None = None,
) -> list[LLMMessage]:
    brief = await scene_brief(session, scene)
    lines = [
        f"SCENE: {brief.as_text()}",
        f"ACTION: {action_text}",
        f"OUTCOME: {outcome}",
    ]
    if target_ref:
        lines.append(f"TARGET: {target_ref}")
    return [
        {"role": "system", "content": CONSEQUENCE_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


# --- Phase 8: narration ------------------------------------------------------
async def build_narration_context(
    session: AsyncSession, *, action_text: str, outcome: str, result_summary: str,
    scene: Scene | None, target_ref: str | None = None,
) -> list[LLMMessage]:
    brief = await scene_brief(session, scene)
    lines = [
        f"SCENE: {brief.as_text()}",
        f"ACTION: {action_text}",
        f"OUTCOME: {outcome}",
        f"RESULT: {result_summary}",
    ]
    if target_ref:
        lines.append(f"TARGET: {target_ref}")
    return [
        {"role": "system", "content": THAI_DM_STYLE + "\n" + NARRATOR_SYSTEM_EXTRA},
        {"role": "user", "content": "\n".join(lines)},
    ]


# --- Phase 11: NPC response (epistemic-scoped) -------------------------------
async def build_npc_response_context(
    session: AsyncSession, *, npc, listener_ref: str, utterance: str,
) -> list[LLMMessage]:
    """Assemble an NPC prompt from ONLY that NPC's own epistemic records. It never
    touches objective KnowledgeRecord/Secret, so unlearned truth cannot leak in."""
    from app.npcs.knowledge_service import NPCKnowledgeService

    facts = await NPCKnowledgeService(session).facts_npc_may_use(npc.id)
    known = "\n".join(f"- [{f.status}] {f.subject}: {f.fact}" for f in facts) or "- (ไม่มีข้อมูลพิเศษ)"
    persona = (
        f"ชื่อ={npc.name}; บุคลิก={npc.personality or '-'}; "
        f"น้ำเสียง={npc.voice_register or '-'}; อารมณ์={npc.emotional_state}"
    )
    return [
        {"role": "system", "content": NPC_RESPONSE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"NPC: {persona}\n"
                f"KNOWN_TO_NPC:\n{known}\n"
                f"LISTENER: {listener_ref}\n"
                f"UTTERANCE: {utterance}"
            ),
        },
    ]


# --- Phase 9: recap (visibility-enforced) ------------------------------------
async def build_recap_context(
    session: AsyncSession, *, campaign_id: str, session_id: str,
    allowed_visibilities: list[Visibility] | None = None,
) -> list[LLMMessage]:
    """Player-safe recap. Only PUBLIC/PARTY events are retrieved — DM_ONLY and
    PLAYER_ONLY facts are filtered out by the SQL query, not by asking the model."""
    allowed = allowed_visibilities or [Visibility.PUBLIC, Visibility.PARTY]
    events = await EventService(session).list_visible_events(
        campaign_id=campaign_id, session_id=session_id, allowed_visibilities=allowed,
    )
    lines = ["EVENTS:"]
    for e in events:
        summary = e.payload.get("summary") if isinstance(e.payload, dict) else None
        lines.append(f"- [{e.event_type}] {summary or ''}".rstrip())
    return [
        {"role": "system", "content": THAI_DM_STYLE + "\n" + RECAP_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]
