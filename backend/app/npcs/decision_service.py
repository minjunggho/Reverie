"""NPCDecisionService — the private, structured decision an NPC makes BEFORE it
speaks (§10).

The engine, not the language model, decides how an NPC reacts: whether it recognizes
this specific listener, what it recalls, its stance and willingness, what it will
share or hide, and whether the request needs a mechanical roll. Only after that
decision is computed and validated does dialogue get generated — the model renders
the decision into words, it does not invent the reaction.

Everything here is derived from ALREADY-COMMITTED state through the existing systems —
`NPCMemoryService.recall` (per-listener relationship + episodic memories),
`classify_interaction`, and `NPCKnowledgeService.facts_npc_may_use` (the retrieval
boundary). Innate `NPC.biases` modulate the reaction only when the campaign's bias
level permits, and never touch objective truth: willingness governs disclosure, it
can never make a fact the NPC never learned appear, nor change canon.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RulesViolation
from app.models.character import Character
from app.models.enums import KnowledgeStatus
from app.models.npc import NPC
from app.models.npc_epistemic import RELATIONSHIP_DIMENSIONS, NPCRelationship
from app.npcs.knowledge_service import NPCKnowledgeService
from app.npcs.memory_service import NPCMemoryService, classify_interaction

# Willingness ladder, most cooperative (6) → most hostile (0).
_WILLINGNESS = ("hostile", "refusing", "resistant", "guarded", "neutral",
                "forthcoming", "eager")
WILLINGNESS_VALUES = frozenset(_WILLINGNESS)

# Bias strength by campaign level. OFF is absent → no bias is ever applied.
_BIAS_STRENGTH = {"LIGHT": 1, "MODERATE": 2, "CENTRAL_THEME": 3}

# Memory types that mean "this listener has been leaning on / hurting this NPC" —
# each one that already happened raises the escalation of a repeated request.
_PRESSURE_TYPES = frozenset({"THREAT", "INSULT", "ASSAULT", "PRESSURE", "LIE"})

# Utterance cues that make a line a REQUEST/attempt (something the NPC must decide to
# grant or refuse) rather than a greeting or statement.
_REQUEST_CUES = ("ขอ", "บอก", "เปิด", "ช่วย", "โปรด", "อยากรู้", "จะเอา", "ให้ข้า",
                 "tell", "give", "open", "help", "please", "let me", "where is",
                 "ขู่", "threat", "or else")


@dataclass
class NPCDecision:
    """The private structured decision, validated before any narration."""

    npc_id: str
    listener_ref: str
    recognized_listener: bool
    recalled_memory_ids: list[str]
    current_stance: str
    emotional_response: str
    immediate_goal: str
    willingness: str
    intended_action: str
    information_to_share: list[str]
    information_to_hide: list[str]
    relationship_deltas: dict[str, int]
    belief_deltas: list[dict] = field(default_factory=list)
    requires_mechanical_resolution: bool = False
    bias_applied: str | None = None
    reason: str = ""

    def as_prompt_block(self, listener_name: str) -> str:
        """A compact, authoritative constraint block for the dialogue generator: the
        model must render THIS reaction, not invent its own."""
        share = ", ".join(self.information_to_share) or "(ไม่มี)"
        hide = ", ".join(self.information_to_hide) or "(ไม่มี)"
        rec = "จำได้" if self.recognized_listener else "ไม่คุ้นหน้า"
        return (
            f"DECISION (เอนจินกำหนด — ต้องแสดงตามนี้ ห้ามขัด):\n"
            f"- ต่อ {listener_name}: {rec}; stance={self.current_stance}; "
            f"อารมณ์={self.emotional_response}; ท่าที={self.willingness}\n"
            f"- ตั้งใจจะ: {self.intended_action}; เป้าหมายเฉพาะหน้า: {self.immediate_goal}\n"
            f"- เปิดเผยได้: {share}\n- ปิดไว้ (ห้ามหลุด): {hide}"
        )


class NPCDecisionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def decide(
        self, *, npc: NPC, listener_ref: str, utterance: str, game_time: int = 0,
        bias_level: str = "OFF", forbidden_bias_kinds: frozenset[str] = frozenset(),
    ) -> NPCDecision:
        recalled = await NPCMemoryService(self.session).recall(
            npc_id=npc.id, listener_ref=listener_ref, game_time=game_time)
        rel = recalled.relationship
        memories = recalled.memories

        recognized = rel is not None and (
            int(getattr(rel, "familiarity", 0) or 0) > 0 or bool(memories))
        stance = (rel.current_stance if rel and rel.current_stance else "neutral")

        # Innate bias modulates the WORKING reaction only (never persisted as an
        # earned relationship), and only when the campaign allows it.
        listener = await self._listener_character(listener_ref)
        bias_delta, bias_desc = _bias_for(
            npc, listener, bias_level, forbidden_bias_kinds)

        escalation = sum(1 for m in memories if m.memory_type in _PRESSURE_TYPES)
        idx = _willingness_index(rel, escalation=escalation, bias_delta=bias_delta)
        willingness = _WILLINGNESS[idx]

        is_request = _looks_like_request(utterance)
        share, hide = await self._disclosure(npc.id, idx)
        emotional = _emotional_response(rel)
        action = _intended_action(idx, is_request, rel)
        goal = _immediate_goal(npc, stance)
        needs_roll = bool(is_request and 1 <= idx <= 4)

        decision = NPCDecision(
            npc_id=npc.id, listener_ref=listener_ref,
            recognized_listener=recognized,
            recalled_memory_ids=[m.id for m in memories],
            current_stance=stance, emotional_response=emotional,
            immediate_goal=goal, willingness=willingness, intended_action=action,
            information_to_share=share, information_to_hide=hide,
            relationship_deltas={k: v for k, v in classify_interaction(utterance).deltas.items()
                                 if k in RELATIONSHIP_DIMENSIONS},
            requires_mechanical_resolution=needs_roll,
            bias_applied=bias_desc,
            reason=_reason(recognized, stance, willingness, escalation, bias_desc),
        )
        validate_decision(decision)
        return decision

    async def _listener_character(self, listener_ref: str) -> Character | None:
        from app.core.ids import parse_entity_ref

        kind, cid = parse_entity_ref(listener_ref)
        if kind == "character" and cid:
            return await self.session.get(Character, cid)
        return None

    async def _disclosure(self, npc_id: str, idx: int) -> tuple[list[str], list[str]]:
        """What the NPC will share vs. hide — drawn ONLY from what it actually knows
        (facts_npc_may_use), gated by willingness. Never objective truth it never
        learned. Cooperative → share all; middling → hide the sensitive; unwilling →
        volunteer nothing."""
        usable = await NPCKnowledgeService(self.session).facts_npc_may_use(npc_id)
        subjects = [f.subject for f in usable]
        # Sensitive = the NPC's secrets and its unconfirmed guesses; guarded even from
        # a trusted listener (an eager NPC still doesn't blurt a secret).
        sensitive = {f.subject for f in usable
                     if f.subject.startswith("secret")
                     or f.status in (KnowledgeStatus.SUSPECTS.value,
                                     KnowledgeStatus.HEARD_RUMOR.value)}
        if idx >= 3:                                   # eager / forthcoming / neutral / guarded
            return [s for s in subjects if s not in sensitive], sorted(sensitive)
        return [], subjects                            # resistant / refusing / hostile


def validate_decision(decision: NPCDecision) -> None:
    """Reject an internally-incoherent decision BEFORE it reaches narration."""
    if decision.willingness not in WILLINGNESS_VALUES:
        raise RulesViolation(f"invalid willingness {decision.willingness!r}")
    for dim, val in decision.relationship_deltas.items():
        if dim not in RELATIONSHIP_DIMENSIONS:
            raise RulesViolation(f"invalid relationship dimension {dim!r}")
        if not -100 <= int(val) <= 100:
            raise RulesViolation(f"relationship delta out of range: {dim}={val}")
    overlap = set(decision.information_to_share) & set(decision.information_to_hide)
    if overlap:
        raise RulesViolation(f"share/hide overlap: {sorted(overlap)}")
    if decision.willingness in ("refusing", "hostile") and decision.information_to_share:
        raise RulesViolation("a refusing/hostile NPC volunteers nothing")


# --- deterministic helpers ------------------------------------------------------

def _willingness_index(rel: NPCRelationship | None, *, escalation: int, bias_delta: int) -> int:
    if rel is None:
        return max(0, min(6, 4 + bias_delta))          # a stranger: neutral ± bias
    warmth = (int(rel.trust or 0) + int(rel.affection or 0)
              + int(rel.respect or 0) + int(rel.obligation or 0))
    hostility = int(rel.anger or 0) + int(rel.suspicion or 0)
    fear = int(rel.fear or 0)
    idx = 4
    if warmth >= 50:
        idx += 2
    elif warmth >= 25:
        idx += 1
    if hostility >= 45:
        idx -= 3
    elif hostility >= 30:
        idx -= 2
    elif hostility >= 15:
        idx -= 1
    idx -= max(0, escalation - 1)                       # repeated pressure escalates
    idx += bias_delta
    if fear >= 20 and hostility < 20:                   # fearful compliance, not warmth
        idx = min(idx, 3)
    return max(0, min(6, idx))


def _emotional_response(rel: NPCRelationship | None) -> str:
    if rel is None:
        return "calm"
    fear, anger = int(rel.fear or 0), int(rel.anger or 0)
    if fear >= 20 and fear >= anger:
        return "fearful"
    if anger >= 20:
        return "angry"
    if int(rel.suspicion or 0) >= 20:
        return "suspicious"
    if int(rel.affection or 0) >= 20:
        return "warm"
    if int(rel.trust or 0) >= 20:
        return "trusting"
    if int(rel.obligation or 0) >= 25:
        return "indebted"
    return "calm"


def _intended_action(idx: int, is_request: bool, rel: NPCRelationship | None) -> str:
    if idx == 0:
        return "call_for_help" if rel and int(rel.fear or 0) >= 30 else "threaten"
    if idx == 1:
        return "refuse"
    if idx == 2:
        return "deflect"
    if not is_request:
        return "greet"
    if idx == 3:
        return "answer_guardedly"
    if idx == 4:
        return "answer"
    if idx == 5:
        return "cooperate"
    return "help"


def _immediate_goal(npc: NPC, stance: str) -> str:
    goals = npc.goals or []
    if goals:
        return str(goals[0])
    return {"hostile": "ขับไล่ภัยคุกคาม", "afraid": "เอาตัวรอด",
            "wary": "ระวังตัวไว้ก่อน", "loyal": "ช่วยเหลือคนที่ไว้ใจ"}.get(stance, "ทำหน้าที่ของตนต่อไป")


def _looks_like_request(utterance: str) -> bool:
    low = (utterance or "").lower()
    return any(cue in low for cue in _REQUEST_CUES)


def _bias_for(
    npc: NPC, listener: Character | None, bias_level: str,
    forbidden: frozenset[str],
) -> tuple[int, str | None]:
    """The innate-bias adjustment to willingness, or (0, None). Gated by the campaign
    bias level and Session-Zero forbidden kinds; only NPCs with matching bias data are
    affected. The first matching entry wins (biases never stack)."""
    strength = _BIAS_STRENGTH.get((bias_level or "OFF").upper())
    if not strength or listener is None or not npc.biases:
        return 0, None
    attrs = {"ancestry": (listener.species or "").lower(),
             "class": (listener.char_class or "").lower(),
             "culture": (listener.background or "").lower()}
    for entry in npc.biases:
        kind = str(entry.get("kind", "")).lower()
        if kind in forbidden or kind not in attrs:
            continue
        target = str(entry.get("target", "")).lower()
        if target and target == attrs[kind]:
            polarity = str(entry.get("polarity", "negative")).lower()
            signed = strength if polarity == "positive" else -strength
            desc = f"{polarity} bias vs {kind}:{target} (level {bias_level})"
            return signed, desc
    return 0, None


def _reason(recognized: bool, stance: str, willingness: str,
            escalation: int, bias_desc: str | None) -> str:
    parts = [("จำคนนี้ได้" if recognized else "ไม่คุ้นหน้าคนนี้"),
             f"stance={stance}", f"ท่าที={willingness}"]
    if escalation > 1:
        parts.append(f"ถูกกดดันซ้ำ {escalation} ครั้ง (ยิ่งไม่ยอม)")
    if bias_desc:
        parts.append(bias_desc)
    return "; ".join(parts)
