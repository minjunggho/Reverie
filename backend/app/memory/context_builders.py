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
    CHECK_SETUP_SYSTEM,
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
    speaker_name: str | None = None, directory=None,
) -> list[LLMMessage]:
    brief = await scene_brief(session, scene)
    speaker = f"SPEAKER: {speaker_name}\n" if speaker_name else ""
    dir_block = _directory_block(directory)
    dir_line = dir_block + "\n" if dir_block else ""
    return [
        {"role": "system", "content": CLASSIFIER_SYSTEM},
        {"role": "user", "content": f"SCENE: {brief.as_text()}\n{dir_line}{speaker}MESSAGE: {message_text}"},
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
    resolved_targets=None, scene_context=None, pacing=None,
    consequence_class=None, narration_hint: str = "", character_context=None,
    progression_context=None, stall_state=None,
) -> list[LLMMessage]:
    lines = []
    if scene_context is not None:
        # Canonical location the narrator frames FROM (so it never invents scenery).
        lines.append(scene_context.location_block())
    else:
        brief = await scene_brief(session, scene)
        lines.append(f"SCENE: {brief.as_text()}")
    # Only present when the party has actually been circling; silent otherwise.
    if stall_state is not None:
        block = stall_state.as_block()
        if block:
            lines.append(block)
    # Where the campaign is going. Without this the narrator can only react to the
    # last message — it has no way to know the campaign has a direction at all.
    if progression_context is not None:
        block = progression_context.as_block()
        if block:
            lines.append(block)
    dir_block = _directory_block(directory)
    if dir_block:
        lines.append(dir_block)
    if pacing is not None:
        lines.append(f"NARRATIVE_PACING: {pacing}")
    lines += [f"ACTION: {action_text}", f"OUTCOME: {outcome}", f"RESULT: {result_summary}"]
    if consequence_class is not None:
        lines.append(f"CONSEQUENCE_CLASS: {consequence_class}")
    if narration_hint:
        lines.append(f"NARRATION_HINT: {narration_hint}")
    tgt = _targets_block(resolved_targets)
    if tgt:
        lines.append(tgt)
    elif target_ref:
        lines.append(f"TARGET: {target_ref}")
    if character_context is not None:
        block = character_context.as_block()
        if block:
            lines.append(block)
    return [
        {"role": "system", "content": THAI_DM_STYLE + "\n" + NARRATOR_SYSTEM_EXTRA},
        {"role": "user", "content": "\n".join(lines)},
    ]


# --- E: fiction-first pre-roll setup ------------------------------------------
async def build_check_setup_context(
    session: AsyncSession, *, action_text: str, check_label: str, scene: Scene | None,
    directory=None, scene_context=None, character_context=None, pacing=None,
) -> list[LLMMessage]:
    """Deliberately excludes DC/outcome — the roll has not happened yet. Only
    canonical/observable facts and the bounded character context are supplied."""
    lines = []
    if scene_context is not None:
        lines.append(scene_context.location_block())
    else:
        brief = await scene_brief(session, scene)
        lines.append(f"SCENE: {brief.as_text()}")
    dir_block = _directory_block(directory)
    if dir_block:
        lines.append(dir_block)
    if pacing is not None:
        lines.append(f"NARRATIVE_PACING: {pacing}")
    lines.append(f"ACTION: {action_text}")
    lines.append(f"PENDING_CHECK: {check_label}")
    if character_context is not None:
        block = character_context.as_block()
        if block:
            lines.append(block)
    return [
        {"role": "system", "content": THAI_DM_STYLE + "\n" + CHECK_SETUP_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


# --- Phase 11: NPC response (epistemic-scoped) -------------------------------
async def build_npc_response_context(
    session: AsyncSession, *, npc, listener_ref: str, utterance: str,
    listener_name: str | None = None, game_time: int = 0,
    decision_block: str | None = None,
) -> list[LLMMessage]:
    """Assemble an NPC prompt from ONLY that NPC's own epistemic records + the
    campaign protocols it is authorized to know + how it feels about and what it
    remembers of THIS specific listener. It never touches objective
    KnowledgeRecord/Secret, so unlearned truth cannot leak in; and one NPC's
    memories are never visible to another (retrieval is scoped to npc.id)."""
    from app.npcs.knowledge_service import NPCKnowledgeService
    from app.npcs.memory_service import NPCMemoryService

    knowledge = NPCKnowledgeService(session)
    facts = await knowledge.facts_npc_may_use(npc.id)
    known = "\n".join(f"- [{f.status}] {f.subject}: {f.fact}" for f in facts) or "- (ไม่มีข้อมูลพิเศษ)"
    protocols = await knowledge.protocols_known_by(campaign_id=npc.campaign_id, npc_name=npc.name)
    proto_block = ""
    if protocols:
        lines = ["PROTOCOLS_KNOWN_TO_NPC (ห้ามเพิ่ม/ตัด/สลับลำดับ/เปลี่ยนความหมายเมื่อถูกถาม):"]
        for p in protocols:
            lines.append(f"- {p['title']}:")
            lines.extend(f"  {i + 1}. {rule}" for i, rule in enumerate(p["rules"]))
        proto_block = "\n".join(lines) + "\n"
    # Retrieval-scoped relationship + episodic memories about THIS listener.
    recalled = await NPCMemoryService(session).recall(
        npc_id=npc.id, listener_ref=listener_ref, game_time=game_time)
    memory_block = recalled.as_prompt_block(listener_name or listener_ref)
    memory_section = (f"MEMORY_OF_LISTENER (ตอบให้สอดคล้องกับความรู้สึก/ความทรงจำนี้):\n"
                      f"{memory_block}\n") if memory_block else ""
    # Campaign-active, per-NPC religious knowledge only. Secret character belief
    # is absent until this particular NPC has legitimately learned it.
    religious_section = ""
    from app.core.ids import parse_entity_ref
    listener_kind, listener_id = parse_entity_ref(listener_ref)
    if listener_kind == "character" and listener_id:
        from app.services.religious_interactions import ReligiousInteractionService

        religious = await ReligiousInteractionService(session).build_context(
            campaign_id=npc.campaign_id, npc_id=npc.id, character_id=listener_id,
        )
        block = religious.as_prompt_block()
        if block:
            religious_section = block + "\n"
    persona = (
        f"ชื่อ={npc.name}; บุคลิก={npc.personality or '-'}; "
        f"น้ำเสียง={npc.voice_register or '-'}; อารมณ์={npc.emotional_state}; "
        f"การสื่อสาร={npc.communication_mode}"
    )
    listener_line = f"LISTENER: {listener_name or listener_ref} ({listener_ref})"
    # The engine's pre-computed decision (recognition/stance/willingness/what may be
    # disclosed) is authoritative — the model renders it into words, never overrides.
    decision_section = f"{decision_block}\n" if decision_block else ""
    return [
        {"role": "system", "content": NPC_RESPONSE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"NPC: {persona}\n"
                f"KNOWN_TO_NPC:\n{known}\n"
                f"{proto_block}"
                f"{memory_section}"
                f"{religious_section}"
                f"{decision_section}"
                f"{listener_line}\n"
                f"UTTERANCE: {utterance}"
            ),
        },
    ]


# --- E5: canonical scene framing (travel arrival / scene open) ---------------
def build_scene_frame_context(scene_context, *, arrival_from: str | None = None) -> list[LLMMessage]:
    """Frame a scene from CANONICAL context only. The location block is authored
    truth; the framer reformats it, never invents. Anti-hallucination lives here by
    construction — the model is given the world, not asked to make it up."""
    from app.ai.prompts.system_prompts import SCENE_FRAMER_SYSTEM

    lines = []
    if arrival_from:
        lines.append(f"ARRIVING_FROM: {arrival_from}")
    lines.append(scene_context.location_block())
    return [
        {"role": "system", "content": THAI_DM_STYLE + "\n" + SCENE_FRAMER_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
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
