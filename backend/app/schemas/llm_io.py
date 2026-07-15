"""Structured schemas for every LLM job I/O.

Guiding rule: the LLM returns *typed proposals and prose*, never raw database
mutations and never numbers it is forbidden to own (dice, modifiers, HP totals).
The engine converts validated proposals into state changes.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.models.enums import (
    ConsequenceClass,
    DifficultyBand,
    MessageCategory,
    ResolutionType,
)


# --- Classification (non-`!` messages) --------------------------------------
class ClassificationResult(BaseModel):
    category: MessageCategory
    confidence: float = Field(ge=0.0, le=1.0)
    # Optional short DM reply for questions / dialogue. Never a state mutation.
    suggested_response: Optional[str] = None
    # CHARACTER_DIALOGUE only: who the line is addressed to, as written (e.g. "Mother
    # Veyra"). The ENGINE resolves this against the present cast; the model never
    # picks an NPC id, and an empty list is NOT "the first NPC in the scene."
    target_references: list[str] = Field(default_factory=list)


# --- Ordered action plan (compound `!` actions) ------------------------------
STEP_KINDS = (
    "SPEAK", "ATTACK", "CAST", "INTERACT", "SEARCH", "MOVE", "HIDE", "USE_ITEM",
    "TRANSFER_ITEM", "TRANSFER_CURRENCY", "WAIT", "OTHER",
)


class ActionStep(BaseModel):
    """One step of an ordered action. Compound input like 'ต่อยยาม หยิบจดหมาย
    แล้ววิ่งหนี' becomes three steps executed IN ORDER; an earlier step's
    consequence may prevent a later one. `temporal` separates what the player is
    doing NOW from what they only SAID (dialogue/future intention/flavor), which
    is preserved but NEVER executed as a physical action."""
    kind: Literal[
        "SPEAK", "ATTACK", "CAST", "INTERACT", "SEARCH", "MOVE", "HIDE",
        "USE_ITEM", "TRANSFER_ITEM", "TRANSFER_CURRENCY", "WAIT", "OTHER",
    ] = "OTHER"
    text: str = ""                       # the clause, verbatim (preserves intent)
    targets: list[str] = Field(default_factory=list)
    method: str = ""
    destination: str = ""                # MOVE: exit/place phrase
    spell_reference: str = ""            # CAST: the spell name as spoken
    item_reference: str = ""             # USE_ITEM/TRANSFER_ITEM
    amount: str = ""                     # TRANSFER_CURRENCY
    condition: str = ""                  # "if the guard turns" — a declared condition
    depends_on_previous: bool = False    # only run if the prior step succeeded
    # IMMEDIATE = do it now; FUTURE = the player only stated an intention ("เดี๋ยว
    # จะไป"); FLAVOR = pure description. FUTURE/FLAVOR are recorded, not executed.
    temporal: Literal["IMMEDIATE", "FUTURE", "FLAVOR"] = "IMMEDIATE"


class ActionPlan(BaseModel):
    actor_ref: str = ""
    steps: list[ActionStep] = Field(default_factory=list)
    confidence: float = 1.0
    ambiguity: str = ""                  # what is unclear, if anything
    clarification_question: str = ""     # ONE focused question, only if it matters

    @property
    def executable_steps(self) -> list[ActionStep]:
        return [s for s in self.steps if s.temporal == "IMMEDIATE"]


# --- Action interpretation (`!` messages) -----------------------------------
class ActionInterpretation(BaseModel):
    goal: str
    method: str
    target_references: list[str] = Field(default_factory=list)
    declared_constraints: list[str] = Field(default_factory=list)
    risk_awareness: list[str] = Field(default_factory=list)
    intent_confidence: float = Field(ge=0.0, le=1.0)
    missing_information: list[str] = Field(default_factory=list)
    # True when the action tries to dictate ANOTHER player character's *voluntary*
    # choice (e.g. "Aria follows me", "Aria opens the door"). A physical action
    # involving another PC who cannot choose (dragging an unconscious ally) is False.
    # The engine refuses to execute when this is True (PC agency is inviolable).
    commands_other_pc: bool = False
    # Movement: the action is primarily going somewhere. `movement_reference` is the
    # exit/destination phrase ("ข้างนอก", "มหาวิหาร", "ชั้นสอง"). The ENGINE resolves
    # it against the world graph; the LLM never picks the destination.
    movement_intent: bool = False
    movement_reference: str = ""
    # Finer movement category (kept alongside `movement_intent` for backward
    # compatibility: scripts/callers that only set `movement_intent` and leave this
    # at "NONE" get the pre-existing behavior). Only CANONICAL_TRAVEL/RETURN_OR_EXIT/
    # SEARCH_FOR_PLACE reach the world graph; only SEARCH_FOR_PLACE (or the legacy
    # unset default) may trigger WorldExpansionService. FOLLOW_SOURCE/LOCAL_MOVEMENT
    # never leave the current Location or create one.
    movement_kind: Literal[
        "NONE", "CANONICAL_TRAVEL", "LOCAL_MOVEMENT", "FOLLOW_SOURCE",
        "SEARCH_FOR_PLACE", "RETURN_OR_EXIT", "REST",
    ] = "NONE"
    # True when the action is fundamentally a voluntary NPC interaction (asking,
    # greeting, thanking, threatening, bargaining, telling, requesting a decision)
    # rather than a physical/mechanical action. Routes to NPCSocialService instead
    # of the generic narrator, which must never invent NPC dialogue or facts.
    social_intent: bool = False
    # Resting/sleeping. `rest_kind` is only meaningful when `rest_intent` is True.
    rest_intent: bool = False
    rest_kind: Literal["short", "long", "ambiguous"] = "ambiguous"
    rest_scope: Literal["actor", "party_request"] = "actor"
    # Spellcasting: the action is fundamentally casting a spell. `spell_reference`
    # is the player's name for it ("ลูกไฟ", "fire bolt", "cure_wounds") — resolved
    # by the ENGINE against the caster's known/prepared pool via the existing
    # spell resolver; the LLM never picks the spell key or the mechanical result.
    # `target_references` (above) name who it hits. slot_level/metamagic are optional.
    cast_intent: bool = False
    spell_reference: str = ""
    slot_level: int | None = None
    metamagic: str = ""
    # Activating a CLASS FEATURE ("ใช้ Second Wind", "เข้าโหมดเกรี้ยวกราด"). The engine
    # resolves the feature against the character's granted features + spends its
    # resource; the LLM only names the feature. `feature_reference` is that name.
    activate_intent: bool = False
    feature_reference: str = ""
    # Natural following (reuses the consent/follow system): "ฉันตาม Kael ไป" sets
    # follow_intent + follow_reference; "ฉันหยุดตาม"/"ฉันอยู่ที่นี่" sets stop_following.
    # The engine resolves the leader + enforces co-location consent; the LLM only
    # names who is being followed.
    follow_intent: bool = False
    follow_reference: str = ""
    stop_following: bool = False
    # Handing an object to someone present ("ส่งขวดให้ Bront"): the ENGINE validates
    # possession + presence and commits the transfer; the LLM only names what and to
    # whom, exactly as spoken. Never narrate a hand-over — the engine does it.
    give_intent: bool = False
    give_item_reference: str = ""
    give_target_reference: str = ""
    # Ordered compound steps, when the action is more than one thing ("A แล้ว B แล้ว
    # C"). Empty for a simple single action (the common case) — the pipeline then
    # uses the flat flags above exactly as before (fully backward compatible).
    steps: list[ActionStep] = Field(default_factory=list)


# --- Adjudication ------------------------------------------------------------
class AdjudicationDecision(BaseModel):
    needs_clarification: bool = False
    clarification_question: Optional[str] = None  # Thai, one focused question

    resolution_type: ResolutionType = ResolutionType.ABILITY_CHECK
    ability: Optional[str] = None   # "str"|"dex"|"con"|"int"|"wis"|"cha"
    skill: Optional[str] = None     # e.g. "stealth", "perception"
    dc_band: Optional[DifficultyBand] = None
    advantage: bool = False
    disadvantage: bool = False
    # Opposed checks name the opponent whose passive score the engine reads.
    contested_against: Optional[str] = None
    rationale: str = ""


# --- Clarification model -----------------------------------------------------
class ClarificationResult(BaseModel):
    """The engine's decision (not the LLM's) about whether to pause and ask."""
    needs_clarification: bool
    question: Optional[str] = None  # Thai, one focused question
    reason: str = ""


# --- Consequence proposal ----------------------------------------------------
class ProposedDelta(BaseModel):
    """A single proposed canonical change. The engine validates `kind` against an
    allowlist and checks authority before committing; unknown kinds are rejected.
    """
    kind: str                       # e.g. "advance_time", "raise_suspicion", "hp_change"
    target: Optional[str] = None    # entity ref, e.g. "npc:<id>"
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class ConsequenceProposal(BaseModel):
    consequence_class: ConsequenceClass
    deltas: list[ProposedDelta] = Field(default_factory=list)
    narration_hint: str = ""


# --- Narration ---------------------------------------------------------------
class Narration(BaseModel):
    """Progressive-disclosure narration: short Thai lines in `text` (with line
    breaks), plus an optional single open decision point. The narrator NEVER puts
    mechanics in `text` — the engine renders the committed roll separately."""
    text: str
    style: str = "concise"  # "concise" | "cinematic"
    decision_prompt: Optional[str] = None  # one open question, Thai, or None


# --- Recap -------------------------------------------------------------------
class Recap(BaseModel):
    text: str


# --- NPC response (Phase 11) -------------------------------------------------
class ProposedBeliefDelta(BaseModel):
    npc_id: str
    subject: str
    new_status: str          # KnowledgeStatus value
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = ""


class NPCResponse(BaseModel):
    # Legacy/general display text — kept for backward compatibility. When the NPC's
    # communication_mode is not SPOKEN, the engine (NPCSocialService), not this
    # field, decides the final presentation; it never trusts the model to remember
    # a mute NPC can't talk.
    utterance: str
    spoken_text: Optional[str] = None
    written_text: Optional[str] = None
    nonverbal_action: Optional[str] = None
    proposed_belief_deltas: list[ProposedBeliefDelta] = Field(default_factory=list)
    proposed_attitude: Optional[str] = None


# --- Post-session (Phase 10) -------------------------------------------------
class PostSessionReport(BaseModel):
    player_summary: str
    continuity_report: dict[str, Any] = Field(default_factory=dict)


# --- Guided character creation -------------------------------------------------
class CreationGuidance(BaseModel):
    """One turn of the guided creation conversation. The AI extracts identity fields
    from what the player just said and asks AT MOST one focused next question — and
    only about something NOT already supplied. The engine owns mechanics: proposed
    class/species are validated and the AI never emits stats.

    Backward compatible: `updated_fields` still accepts the legacy hook keys
    (concept/origin/desire/fear/flaw/connection/appearance/name); it now ALSO
    accepts the richer identity fields (pronouns, ancestry, age, eyes, hair, culture,
    homeland, family, mentors, rivals, goals, ideals, bonds, secrets, boundaries, …
    — see app/services/campaigns/identity.IDENTITY_FIELDS)."""
    updated_fields: dict[str, str] = Field(default_factory=dict)
    proposed_class: Optional[str] = None     # canonical class the player implied/stated
    proposed_species: Optional[str] = None   # stated ancestry (may be custom/unbundled)
    proposed_subclass: Optional[str] = None
    # A short, warm, SPECIFIC reaction to what the player just shared (Thai). Not a
    # question — the human touch that makes creation feel like a conversation.
    reaction: str = ""
    next_question: Optional[str] = None      # Thai, ONE question about something MISSING, or None
    ready_to_reveal: bool = False
    reveal_summary: str = ""                 # Thai identity paragraph for the reveal


# --- World expansion (canon-consistent AI location) ----------------------------
class LocationDraft(BaseModel):
    """A canon-consistent ordinary place proposed by WorldExpansionService. The
    engine validates + commits it (provenance AI_EXPANDED) BEFORE any narration;
    once committed it is canonical and must not be reimagined."""
    name: str
    location_type: str = "LOCATION"          # SHOP/HOUSE/ALLEY/ROOM/LOCATION...
    obvious: str = ""                        # player-facing description
    canon_justification: str = ""            # why this fits the settlement (DM-only)
    connection_label: str = "ทางเข้า"        # how it links to the current location
    travel_minutes: int = 0
    npc_name: str = ""                        # optional proprietor
    secret: str = ""                         # usually empty for ordinary places


# --- Session opening (session 1 / hook-aware) ----------------------------------
class OpeningScene(BaseModel):
    """A generated opening. Situation lines follow progressive disclosure; the
    engine renders them — the AI cannot mutate state or invent mechanics here."""
    title: str                               # Thai scene/session title
    situation_lines: list[str] = Field(default_factory=list)   # 3-6 short Thai lines
    pressure: str = ""                       # the disturbance/pressure line
    decision_prompt: str = ""                # one open question
    used_hooks: list[str] = Field(default_factory=list)        # which hooks it drew on


# --- Cinematic campaign prologue (session 1, world-scale) ----------------------
class CampaignPrologue(BaseModel):
    """A grand, cinematic opening prologue for the FIRST session of a campaign.

    It moves from the largest scale down to the party — world → crisis → region →
    settlement → the exact place → the characters — and ends on a first beat that
    hands play to the players. It is player-facing FRAMING built ONLY from the
    supplied canon: it dramatizes atmosphere, stakes, and imagery vividly, but it
    never commits state, never invents new named kingdoms/gods/factions/places
    beyond those given, and never dictates what a player character feels or chooses.
    """
    title: str                               # Thai campaign/opening title
    world: str                               # nature & atmosphere + age/era (layers 1-2)
    powers: str                              # kingdoms/factions/religions + magic, gods, monsters
    crisis: str                              # the major conflict/disaster + how ordinary folk suffer
    approach: str                            # region → settlement/wilderness → the exact place (3-5)
    the_party: str                           # the PCs and why THEY are drawn in (layer 6)
    main_goal: str                           # ONE clear, specific long-term objective
    first_beat: str                          # the immediate event/danger/mystery that starts play
    decision_prompt: str = ""                # the players' clear first choice
    used_hooks: list[str] = Field(default_factory=list)        # character hooks it drew on
