"""What NPCs remember about what players DO — not just what they say.

The gap this closes: `classify_interaction` turns an UTTERANCE into a typed memory and
relationship deltas, and the social path commits it. Nothing did the same for an
ACTION. So a goblin that watched someone try to lift its map recorded nothing at all,
and the next time that person spoke, `recall()` returned an empty slate and the NPC
greeted them like a stranger. The theft was not overwritten by the excuse — the NPC
never knew about it.

Two halves, both deterministic:

  CLASSIFY — (action + outcome + detected) → a typed memory + fixed relationship
             deltas. The same shape as the utterance table, so both loops feed the
             SAME NPCRelationship dimensions that NPCDecisionService already reads.
  WITNESS  — who actually perceived it. Only a witness remembers.

Deliberately not LLM-proposed. A consequence that exists only when a model remembers
to ask for it is the bug, not the fix: an engine-classified consequence always fires.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.logging import get_logger

log = get_logger(__name__)

# How an action was perceived. Distinct from whether it SUCCEEDED: a theft can fail
# and be seen, succeed and be seen, or succeed unnoticed — three different worlds.
DETECTION_WITNESSED = "witnessed"    # they saw who did it
DETECTION_SUSPECTED = "suspected"    # they know something happened
DETECTION_UNNOTICED = "unnoticed"    # nobody registered it


@dataclass(frozen=True)
class ActionMemoryClass:
    """The memory an action leaves in a witness, and what it does to the
    relationship. Mirrors InteractionClass so both loops stay comparable."""
    memory_type: str
    importance: int                 # 0..100 — drives recall ordering
    valence: int                    # -3..3
    deltas: dict[str, int]          # NPCRelationship dimensions
    summary_th: str                 # "{actor} ..." — filled by the caller
    # An unresolved thread the NPC is still carrying: something they want an answer
    # to. This is what keeps a caught theft ALIVE across a change of subject.
    open_question_th: str = ""


# Actions whose consequences the world must not forget. Keyed by an engine action
# class, then by how it landed. A missing (action, detection) pair yields no memory —
# most actions are not memorable, and inventing a consequence is as wrong as losing
# one.
_HOSTILE_ATTEMPT = frozenset({"steal", "pickpocket", "sabotage", "poison"})

ACTION_MEMORY: dict[tuple[str, str, str], ActionMemoryClass] = {
    # --- theft ----------------------------------------------------------------
    ("steal", "failure", DETECTION_WITNESSED): ActionMemoryClass(
        "CAUGHT_STEALING", 90, -3,
        {"trust": -35, "suspicion": 40, "anger": 25, "familiarity": 5},
        "พยายามขโมย{object}ต่อหน้า{npc}แล้วถูกจับได้",
        "ทำไมถึงเอื้อมมือไปที่{object}?"),
    ("steal", "success", DETECTION_WITNESSED): ActionMemoryClass(
        "CAUGHT_STEALING", 95, -3,
        {"trust": -40, "suspicion": 45, "anger": 35, "familiarity": 5},
        "ขโมย{object}ไปต่อหน้า{npc}",
        "{object}หายไปไหน?"),
    ("steal", "failure", DETECTION_SUSPECTED): ActionMemoryClass(
        "SUSPICIOUS_BEHAVIOUR", 60, -2,
        {"trust": -15, "suspicion": 25, "familiarity": 3},
        "ทำอะไรน่าสงสัยใกล้{object}",
        "เมื่อกี้ทำอะไรอยู่ตรงนั้น?"),
    ("steal", "success", DETECTION_SUSPECTED): ActionMemoryClass(
        "SUSPICIOUS_BEHAVIOUR", 65, -2,
        {"trust": -15, "suspicion": 30, "familiarity": 3},
        "อยู่ใกล้{object}ตอนที่มันหายไป",
        "{object}หายไปตอนไหน?"),
    # --- violence ---------------------------------------------------------------
    ("attack", "success", DETECTION_WITNESSED): ActionMemoryClass(
        "ASSAULT", 95, -3,
        {"fear": 40, "anger": 35, "trust": -40, "suspicion": 30},
        "ทำร้าย{target}ต่อหน้า{npc}", "จะเอาเรื่องนี้ยังไงต่อ?"),
    ("attack", "failure", DETECTION_WITNESSED): ActionMemoryClass(
        "ASSAULT", 85, -3,
        {"fear": 30, "anger": 30, "trust": -35, "suspicion": 30},
        "พยายามทำร้าย{target}ต่อหน้า{npc}", "จะเอาเรื่องนี้ยังไงต่อ?"),
    # --- deception caught -------------------------------------------------------
    ("deceive", "failure", DETECTION_WITNESSED): ActionMemoryClass(
        "LIE", 70, -2,
        {"trust": -30, "suspicion": 30, "anger": 10, "familiarity": 3},
        "โกหก{npc}แล้วถูกจับได้", "ทำไมถึงโกหก?"),
    ("deceive", "success", DETECTION_WITNESSED): ActionMemoryClass(
        "INTERACTION", 20, 0, {"familiarity": 3},
        "อธิบายบางอย่างให้{npc}ฟัง"),
    # --- trespass / intrusion ---------------------------------------------------
    ("trespass", "failure", DETECTION_WITNESSED): ActionMemoryClass(
        "SUSPICIOUS_BEHAVIOUR", 70, -2,
        {"trust": -20, "suspicion": 35, "anger": 15},
        "ถูกจับได้ว่าเข้ามาในที่ที่ไม่ควรอยู่", "เข้ามาทำไม?"),
    ("trespass", "success", DETECTION_SUSPECTED): ActionMemoryClass(
        "SUSPICIOUS_BEHAVIOUR", 50, -1,
        {"suspicion": 20}, "อาจเคยเข้ามาในที่หวงห้าม", "ใครเข้ามาในนี้?"),
    # --- help -------------------------------------------------------------------
    ("help", "success", DETECTION_WITNESSED): ActionMemoryClass(
        "HELP", 60, 2,
        {"trust": 20, "obligation": 25, "respect": 10, "affection": 10},
        "ช่วย{npc}เอาไว้"),
    ("heal", "success", DETECTION_WITNESSED): ActionMemoryClass(
        "RESCUE", 85, 3,
        {"trust": 30, "obligation": 40, "respect": 20, "affection": 15},
        "รักษา{target}ต่อหน้า{npc}"),
}

# Skills a player uses when the ACTION ITSELF is the crime. Used to classify an
# action the interpreter did not otherwise label.
SKILL_TO_ACTION = {
    "sleight_of_hand": "steal",
    "stealth": "trespass",
    "deception": "deceive",
    "athletics": "force",
    "intimidation": "coerce",
}


@dataclass
class WitnessedAction:
    """One committed action, as the world will remember it."""
    action_class: str
    outcome: str                       # "success" | "failure"
    detection: str
    actor_ref: str
    actor_name: str
    object_name: str = ""
    target_name: str = ""
    witnesses: list[str] = field(default_factory=list)   # npc entity refs

    @property
    def memorable(self) -> bool:
        return self.detection != DETECTION_UNNOTICED and bool(self.witnesses)


def classify_action(action_class: str | None, outcome: str,
                    detection: str) -> ActionMemoryClass | None:
    """The memory this action leaves, or None when it leaves none.

    None is the common and correct answer: walking across a room is not a memory.
    """
    if not action_class:
        return None
    return ACTION_MEMORY.get((action_class, outcome, detection))


def detection_for(*, outcome: str, action_class: str | None,
                  passive_noticed: bool) -> str:
    """How an action landed in the world's perception.

    A FAILED hostile attempt is witnessed by anyone watching — fumbling a hand into
    someone's pack is exactly how you get caught. A SUCCESSFUL one is only noticed if
    an observer actually clocked it, which is what `passive_noticed` carries.
    """
    if action_class in _HOSTILE_ATTEMPT and outcome == "failure":
        return DETECTION_WITNESSED
    if passive_noticed:
        return DETECTION_WITNESSED
    return DETECTION_UNNOTICED


def render_summary(cls: ActionMemoryClass, action: WitnessedAction,
                   npc_name: str) -> str:
    return cls.summary_th.format(
        actor=action.actor_name, npc=npc_name,
        object=action.object_name or "ของ", target=action.target_name or "คนอื่น",
    )


def render_open_question(cls: ActionMemoryClass, action: WitnessedAction,
                         npc_name: str) -> str:
    if not cls.open_question_th:
        return ""
    return cls.open_question_th.format(
        actor=action.actor_name, npc=npc_name,
        object=action.object_name or "ของ", target=action.target_name or "คนอื่น",
    )
