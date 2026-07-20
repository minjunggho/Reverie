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
# Words whose presence in the action means the character is *explicitly* invoking their
# faith — a prayer, a chant, an oath, a battle cry to their god. Substring-matched
# because Thai is written without word boundaries, so a token set would miss "…ด้วยศรัทธา".
# Their presence lets the (PUBLIC) deity NAME reach the narrator so an invoked cry can be
# voiced ("เพื่อ<เทพ>!") — the name is still read from canon, never invented, and a SECRET
# belief is filtered out before this ever runs.
_FAITH_INVOCATION_TERMS = (
    "ศรัทธา", "สวด", "ภาวนา", "อธิษฐาน", "สาบาน", "อวยพร", "ศักดิ์สิทธิ์", "บูชา",
    "เทพเจ้า", "พระเจ้า", "faith", "pray", "bless", "divine", "holy", "oath", "vow",
)


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
    # Faith surfaces ONLY when the moment is religiously relevant (divine casting,
    # prayer, a crisis of faith) — never on every turn. Every value is read from the
    # character's PUBLIC belief profile; a SECRET/PRIVATE belief is filtered out before
    # it can reach a player-facing prompt, and nothing here is invented.
    faith: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (
            self.appearance or self.conditions or self.relevant_hooks
            or self.relationship or self.objective or self.recent_events
            or self.faith
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
        for key, value in self.faith.items():
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
    is_divine_action: bool = False,
    campaign_id: str | None = None,
) -> CharacterNarrativeContext:
    if character is None:
        return CharacterNarrativeContext()

    hooks = dict(character.hooks or {})
    relevance_text = " ".join(
        [action_text, target_name, location_name, threat_name, consequence_hint]
    )
    relevance_words = _words(relevance_text)
    invokes_faith = any(
        term in relevance_text.casefold() for term in _FAITH_INVOCATION_TERMS
    )

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

    faith = await _relevant_faith(
        session, character, campaign_id=campaign_id,
        is_divine_action=is_divine_action, is_saving_throw=is_saving_throw,
        relevance_words=relevance_words, invokes_faith=invokes_faith,
    )

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
        faith=faith,
    )


async def _relevant_faith(
    session: AsyncSession, character: Character, *, campaign_id: str | None,
    is_divine_action: bool, is_saving_throw: bool, relevance_words: set[str],
    invokes_faith: bool = False,
) -> dict[str, str]:
    """The character's faith, surfaced ONLY when the moment is religiously relevant —
    divine casting, an explicit invocation (a prayer/oath/battle cry to their god), or
    the deity/symbol/doubt sharing a keyword with what is happening. Read from the PUBLIC
    belief profile only (a SECRET/PRIVATE belief never reaches a player-facing prompt);
    every value is stored, none invented."""
    raw = character.belief_profile
    if not raw or campaign_id is None:
        return {}
    from app.schemas.belief import DevotionLevel
    from app.services.beliefs import BeliefService

    profile = BeliefService.visible_profile(raw, owner_view=False)  # PUBLIC only
    if profile is None:
        return {}

    deity_name = ""
    if profile.primary_deity_key:
        from app.services.faith import FaithService

        deity = await FaithService(session).get_deity(campaign_id, profile.primary_deity_key)
        deity_name = deity.name_th if deity else ""

    # Relevance gate: divine action always qualifies; otherwise a stored faith term
    # must share a keyword with the moment (a deity named aloud, a shrine, doubt).
    faith_terms = " ".join(
        t for t in (deity_name, profile.sacred_symbol, profile.personal_reason,
                    profile.doubt, profile.religious_conflict) if t
    )
    keyword_hit = bool(_words(faith_terms) & relevance_words)
    if not (is_divine_action or keyword_hit or invokes_faith):
        return {}

    out: dict[str, str] = {}
    if deity_name:
        devotion = "" if profile.devotion == DevotionLevel.NONE else f" ({profile.devotion.value.lower()})"
        out["deity"] = f"{deity_name}{devotion}"
    if profile.sacred_symbol:
        out["sacred_symbol"] = profile.sacred_symbol
    if profile.practices:
        out["practice"] = profile.practices[0]
    # A crisis of faith surfaces only at a fraught moment (a save, or an explicit
    # keyword echo) — not woven into every routine blessing.
    if is_saving_throw or keyword_hit:
        if profile.doubt:
            out["doubt"] = profile.doubt
        elif profile.religious_conflict:
            out["conflict"] = profile.religious_conflict
    return out
