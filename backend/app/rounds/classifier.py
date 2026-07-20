"""Noncombat action-relationship classification.

Before narration, the resolver must know how the submitted actions relate — so the
combined scene shows one player's distraction enabling another's theft, not two isolated
paragraphs. Deterministic heuristics over the structured/verbatim fields; the LLM is
never asked to decide these relationships.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.models.enums import ActionRelationship as R

_FREE = ("free", "release", "unbind", "untie", "save", "ปล่อย", "ช่วย", "แก้เชือก", "ปลด")
_HARM = ("kill", "execute", "slay", "behead", "ฆ่า", "ประหาร", "สังหาร", "เชือด")
_LIFT = ("lift", "hold", "hoist", "brace", "prop", "ยก", "ค้ำ", "ถือ", "ดัน", "งัด")
_UNDER = ("crawl", "slip", "duck", "squeeze", "คลาน", "ลอด", "มุด", "แทรก")
_DISTRACT = ("distract", "lure", "divert", "decoy", "เบี่ยงเบน", "ล่อ", "ดึงความสนใจ")
_TAKE = ("steal", "grab", "take", "pick", "swipe", "ขโมย", "หยิบ", "ฉก", "คว้า")
_SOCIAL = ("talk", "ask", "persuade", "question", "greet", "พูด", "ถาม", "คุย", "โน้มน้าว", "ทัก")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", (text or "")).casefold().strip()


def _has(text: str, words) -> bool:
    low = _norm(text)
    return any(w in low for w in words)


@dataclass
class RelationshipMap:
    per_actor: dict[str, str] = field(default_factory=dict)
    pairs: list[dict] = field(default_factory=list)

    def dependencies(self) -> dict[str, str]:
        """actor_id → the actor_id it must resolve AFTER (sequential edges only)."""
        deps: dict[str, str] = {}
        for p in self.pairs:
            if p["relationship"] == R.SEQUENTIAL.value and p.get("depends_on"):
                dependent = next(a for a in p["actors"] if a != p["depends_on"])
                deps[dependent] = p["depends_on"]
        return deps


_PRIORITY = [R.SECRET, R.INTERRUPTING, R.CONFLICTING, R.SEQUENTIAL,
             R.COOPERATIVE, R.MUTUALLY_EXCLUSIVE, R.SOCIAL_OVERLAP, R.INDEPENDENT]


def classify_relationships(submissions: list[dict]) -> RelationshipMap:
    """`submissions` are serialized intentions (actor_id + structured fields)."""
    rmap = RelationshipMap()
    labels: dict[str, set[str]] = {s["actor_id"]: set() for s in submissions}

    for s in submissions:
        aid = s["actor_id"]
        if s.get("visibility") == "SECRET":
            labels[aid].add(R.SECRET.value)
        if (s.get("reaction_intent") or "").strip():
            labels[aid].add(R.INTERRUPTING.value)

    for i in range(len(submissions)):
        for j in range(i + 1, len(submissions)):
            a, b = submissions[i], submissions[j]
            rel = _pair_relationship(a, b)
            if rel is None:
                continue
            labels[a["actor_id"]].add(rel["relationship"])
            labels[b["actor_id"]].add(rel["relationship"])
            rmap.pairs.append(rel)

    for aid, found in labels.items():
        rmap.per_actor[aid] = next(
            (r.value for r in _PRIORITY if r.value in found), R.INDEPENDENT.value)
    return rmap


def _pair_relationship(a: dict, b: dict) -> dict | None:
    a_txt = f"{a.get('primary_action','')} {a.get('raw_player_text','')}"
    b_txt = f"{b.get('primary_action','')} {b.get('raw_player_text','')}"
    a_tgt, b_tgt = _norm(a.get("action_target")), _norm(b.get("action_target"))
    a_dest, b_dest = _norm(a.get("destination")), _norm(b.get("destination"))
    same_tgt = bool(a_tgt) and a_tgt == b_tgt

    # Conflicting: same target, opposing intent (free vs harm, either direction).
    if same_tgt and (
        (_has(a_txt, _FREE) and _has(b_txt, _HARM))
        or (_has(a_txt, _HARM) and _has(b_txt, _FREE))
    ):
        return {"actors": [a["actor_id"], b["actor_id"]],
                "relationship": R.CONFLICTING.value, "depends_on": None,
                "note": f"เป้าหมายเดียวกัน ({a_tgt}) เจตนาขัดกัน"}

    # Sequential: a distraction enables a theft; the taker resolves AFTER the distractor.
    if _has(a_txt, _DISTRACT) and _has(b_txt, _TAKE):
        return {"actors": [a["actor_id"], b["actor_id"]],
                "relationship": R.SEQUENTIAL.value, "depends_on": a["actor_id"],
                "note": "เบี่ยงเบนก่อน แล้วอีกคนฉวยโอกาส"}
    if _has(b_txt, _DISTRACT) and _has(a_txt, _TAKE):
        return {"actors": [a["actor_id"], b["actor_id"]],
                "relationship": R.SEQUENTIAL.value, "depends_on": b["actor_id"],
                "note": "เบี่ยงเบนก่อน แล้วอีกคนฉวยโอกาส"}
    # Sequential: one action's condition names the other actor.
    if _norm(b["actor_id"]) and b["actor_id"] and _mentions(a.get("condition"), b):
        return {"actors": [a["actor_id"], b["actor_id"]],
                "relationship": R.SEQUENTIAL.value, "depends_on": b["actor_id"],
                "note": "เงื่อนไขของคนหนึ่งอ้างถึงการกระทำของอีกคน"}

    # Cooperative: one lifts/holds while the other slips under, or same object helping.
    if (_has(a_txt, _LIFT) and _has(b_txt, _UNDER)) or (_has(b_txt, _LIFT) and _has(a_txt, _UNDER)):
        return {"actors": [a["actor_id"], b["actor_id"]],
                "relationship": R.COOPERATIVE.value, "depends_on": None,
                "note": "คนหนึ่งยก อีกคนลอดผ่าน"}

    # Mutually exclusive: both move into the same restricted space.
    if a_dest and a_dest == b_dest:
        return {"actors": [a["actor_id"], b["actor_id"]],
                "relationship": R.MUTUALLY_EXCLUSIVE.value, "depends_on": None,
                "note": f"มุ่งไปที่เดียวกัน ({a_dest})"}

    # Social overlap: both speaking to the same target.
    if same_tgt and _has(a_txt, _SOCIAL) and _has(b_txt, _SOCIAL):
        return {"actors": [a["actor_id"], b["actor_id"]],
                "relationship": R.SOCIAL_OVERLAP.value, "depends_on": None,
                "note": "พูดกับคนเดียวกันพร้อมกัน"}
    return None


def _mentions(condition: str | None, other: dict) -> bool:
    if not condition:
        return False
    low = _norm(condition)
    tokens = [t for t in re.split(r"\W+", _norm(other.get("raw_player_text", ""))) if len(t) > 2]
    return any(tok in low for tok in tokens[:8])
