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


def _directory_block(directory) -> str:
    """Render the present cast with canonical names + types (never raw refs alone),
    so the model can tie a name like 'Aria' to an established player character
    instead of inventing a stranger. Bounded: names/types only, no backstories,
    no player-only knowledge."""
    if directory is None:
        return ""
    lines: list[str] = []
    if directory.actor is not None:
        st = f"; สถานะ={directory.actor.observable_state}" if directory.actor.observable_state else ""
        lines.append(f"ACTOR: {directory.actor.canonical_name} [PLAYER_CHARACTER]"
                     f" ({directory.actor.entity_ref}){st}")
    pcs = [e for e in directory.present_player_characters if not e.is_actor]
    if pcs:
        lines.append("PRESENT_PLAYER_CHARACTERS:")
        for e in pcs:
            st = f" · {e.observable_state}" if e.observable_state else ""
            lines.append(f"- {e.canonical_name} [PLAYER_CHARACTER] ({e.entity_ref}){st}")
    npcs = directory.present_npcs
    if npcs:
        lines.append("PRESENT_ENTITIES:")
        for e in npcs:
            alias = f" (aka {', '.join(e.aliases)})" if e.aliases else ""
            lines.append(f"- {e.canonical_name} [NPC]{alias} ({e.entity_ref})")
    return "\n".join(lines)


def _targets_block(resolved_targets) -> str:
    if not resolved_targets:
        return ""
    lines = ["TARGETS:"]
    for e in resolved_targets:
        st = f" · {e.observable_state}" if e.observable_state else ""
        lines.append(f"- {e.canonical_name} [{e.entity_type}] ({e.entity_ref}){st}")
    return "\n".join(lines)


# --- Phase 5: classification -------------------------------------------------
async def build_classification_context(
    session: AsyncSession, *, message_text: str, scene: Scene | None,
    speaker_name: str | None = None,
) -> list[LLMMessage]:
    brief = await scene_brief(session, scene)
    speaker = f"SPEAKER: {speaker_name}\n" if speaker_name else ""
    return [
        {"role": "system", "content": CLASSIFIER_SYSTEM},
        {"role": "user", "content": f"SCENE: {brief.as_text()}\n{speaker}MESSAGE: {message_text}"},
    ]


# --- Phase 6: interpretation (party-aware) -----------------------------------
async def build_action_interpretation_context(
    session: AsyncSession, *, action_text: str, scene: Scene | None,
    character: Character | None, directory=None,
) -> list[LLMMessage]:
    brief = await scene_brief(session, scene)
    parts = [f"SCENE: {brief.as_text()}"]
    dir_block = _directory_block(directory)
    if dir_block:
        parts.append(dir_block)
    else:
        parts.append(f"CHARACTER: {_character_capabilities(character)}")
    parts.append(f"ACTION: {action_text}")
    return [
        {"role": "system", "content": INTERPRETER_SYSTEM},
        {"role": "user", "content": "\n".join(parts)},
    ]


# --- Phase 7: adjudication (typed actor + targets) ---------------------------
async def build_adjudication_context(
    session: AsyncSession, *, action_text: str, interpretation_summary: str,
    scene: Scene | None, character: Character | None, directory=None,
    resolved_targets=None,
) -> list[LLMMessage]:
    brief = await scene_brief(session, scene)
    parts = [f"SCENE: {brief.as_text()}"]
    dir_block = _directory_block(directory)
    parts.append(dir_block if dir_block else f"CHARACTER: {_character_capabilities(character)}")
    tgt = _targets_block(resolved_targets)
    if tgt:
        parts.append(tgt)
    parts.append(f"INTERPRETATION: {interpretation_summary}")
    parts.append(f"ACTION: {action_text}")
    return [
        {"role": "system", "content": ADJUDICATOR_SYSTEM},
        {"role": "user", "content": "\n".join(parts)},
    ]


# --- Phase 7/8: consequence planning -----------------------------------------
async def build_consequence_context(
    session: AsyncSession, *, action_text: str, outcome: str, scene: Scene | None,
    target_ref: str | None = None, resolved_targets=None,
) -> list[LLMMessage]:
    brief = await scene_brief(session, scene)
    lines = [
        f"SCENE: {brief.as_text()}",
        f"ACTION: {action_text}",
        f"OUTCOME: {outcome}",
    ]
    tgt = _targets_block(resolved_targets)
    if tgt:
        lines.append(tgt)
    elif target_ref:
        lines.append(f"TARGET: {target_ref}")
    # Authored fragments this scene may surface (the ONLY legal reveal_fragment texts).
    if scene is not None and (scene.allowed_clues or []):
        lines.append("ALLOWED_CLUES:")
        lines.extend(f"- {clue}" for clue in scene.allowed_clues)
    return [
        {"role": "system", "content": CONSEQUENCE_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


# --- Phase 8: narration ------------------------------------------------------
async def build_narration_context(
    session: AsyncSession, *, action_text: str, outcome: str, result_summary: str,
    scene: Scene | None, target_ref: str | None = None, directory=None,
    resolved_targets=None,
) -> list[LLMMessage]:
    brief = await scene_brief(session, scene)
    lines = [f"SCENE: {brief.as_text()}"]
    dir_block = _directory_block(directory)
    if dir_block:
        lines.append(dir_block)
    lines += [f"ACTION: {action_text}", f"OUTCOME: {outcome}", f"RESULT: {result_summary}"]
    tgt = _targets_block(resolved_targets)
    if tgt:
        lines.append(tgt)
    elif target_ref:
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
