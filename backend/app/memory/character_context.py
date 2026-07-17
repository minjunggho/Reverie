"""CharacterNarrativeContext — a bounded, player-safe narrative profile for the
acting character (issue #1, item C).

Structural guarantee against invention: every field here is read from a column or
event that already exists (`Character.hooks`/`appearance`/`conditions`, PUBLIC/PARTY
events). Nothing is generated. A hook is surfaced only when it is deterministically
relevant to the current moment — the narrator is never handed the full backstory.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import entity_ref
from app.models.character import Character
from app.models.enums import Visibility
from app.services.events import EventService

# The freeform keys a Character.hooks dict may contain (see CreationGuidance).
_BACKGROUND_HOOK_KEYS = ("concept", "origin", "desire", "fear", "flaw")
# Hooks that always matter when the moment is a saving throw — that is precisely
# when established fear/flaw/trauma becomes fictionally relevant (issue #1, item C).
_SAVING_THROW_HOOK_KEYS = ("fear", "flaw")
MAX_HOOKS = 3
MAX_RECENT_EVENTS = 3


def _words(text: str) -> set[str]:
    norm = unicodedata.normalize("NFC", text or "").casefold()
    return set(re.findall(r"[\w]+", norm, flags=re.UNICODE))


@dataclass
class CharacterNarrativeContext:
    name: str = ""
    char_class: str = ""
    species: str = ""
    background: str = ""
    appearance: str = ""
    conditions: list[str] = field(default_factory=list)
    relevant_hooks: dict[str, str] = field(default_factory=dict)
    relationship: str = ""
    objective: str = ""
    recent_events: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.appearance or self.conditions or self.relevant_hooks
            or self.relationship or self.objective or self.recent_events
        )

    def as_block(self) -> str:
        if self.is_empty() and not self.name:
            return ""
        lines = [f"CHARACTER_CONTEXT: {self.name} ({self.char_class}, {self.species})"]
        if self.background:
            lines.append(f"- background: {self.background}")
        if self.appearance:
            lines.append(f"- appearance: {self.appearance}")
        if self.conditions:
            lines.append(f"- conditions: {', '.join(self.conditions)}")
        for key, value in self.relevant_hooks.items():
            lines.append(f"- {key}: {value}")
        if self.relationship:
            lines.append(f"- relationship: {self.relationship}")
        if self.objective:
            lines.append(f"- objective: {self.objective}")
        if self.recent_events:
            lines.append("RECENT_VISIBLE_EVENTS:")
            lines.extend(f"- {e}" for e in self.recent_events)
        return "\n".join(lines)


async def build_character_narrative_context(
    session: AsyncSession,
    *,
    character: Character | None,
    action_text: str = "",
    target_name: str = "",
    location_name: str = "",
    threat_name: str = "",
    consequence_hint: str = "",
    is_saving_throw: bool = False,
    campaign_id: str | None = None,
) -> CharacterNarrativeContext:
    if character is None:
        return CharacterNarrativeContext()

    hooks = dict(character.hooks or {})
    relevance_text = " ".join(
        [action_text, target_name, location_name, threat_name, consequence_hint]
    )
    relevance_words = _words(relevance_text)

    selected: dict[str, str] = {}
    # Saving throws always surface fear/flaw first — the exact moment established
    # trauma is allowed to matter (bounded to the existing hook, never invented).
    if is_saving_throw:
        for key in _SAVING_THROW_HOOK_KEYS:
            value = hooks.get(key)
            if value and len(selected) < MAX_HOOKS:
                selected[key] = value
    for key in _BACKGROUND_HOOK_KEYS:
        if key in selected or len(selected) >= MAX_HOOKS:
            continue
        value = hooks.get(key)
        if not value:
            continue
        if _words(value) & relevance_words:
            selected[key] = value

    recent_events: list[str] = []
    if campaign_id is not None:
        char_ref = entity_ref("character", character.id)
        events = await EventService(session).list_visible_events(
            campaign_id=campaign_id,
            allowed_visibilities=[Visibility.PUBLIC, Visibility.PARTY],
        )
        involving = [
            e for e in events
            if e.actor_entity == char_ref or char_ref in (e.target_entities or [])
        ]
        for e in involving[-MAX_RECENT_EVENTS:]:
            summary = e.payload.get("summary") if isinstance(e.payload, dict) else None
            if summary:
                recent_events.append(summary)

    return CharacterNarrativeContext(
        name=character.name,
        char_class=character.char_class,
        species=character.species,
        background=character.background,
        appearance=character.appearance or "",
        conditions=list(character.conditions or []),
        relevant_hooks=selected,
        relationship=hooks.get("connection", ""),
        objective=hooks.get("objective", ""),
        recent_events=recent_events,
    )
