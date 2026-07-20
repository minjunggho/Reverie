"""Anti-hallucination / world-authoring screen for narrator output.

In AUTHORITATIVE_WORLD mode, Reverie must never ask a player to invent an objective
world fact. This deterministically detects DM output that outsources world authorship
("เจ้าเห็นอะไรข้างนอก?", "เมืองนี้ชื่ออะไร?") and rewrites it to an agency-safe prompt
("Veskan จะทำอย่างไร?"). Questions about the player's OWN character are allowed.

This is a structural guard, not a prompt plea — see docs/world-canon.md.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Phrases that ask the player to author objective world facts (bad in AUTHORITATIVE).
_WORLD_AUTHORING = [
    r"เห็น\s*อะไร",            # "what do you see"
    r"มี\s*อะไร",              # "what is there" (any word order)
    r"มี\s*ใคร(?:อยู่)?",       # "who is there"
    r"(?:เมือง|ที่|ร้าน|ตรอก|ห้อง)\s*นี้\s*(?:ชื่อ|เป็น|มีหน้าตา)\s*(?:อะไร|ยังไง|แบบไหน)",
    r"หน้าตา\s*(?:เป็น|แบบไหน|ยังไง)",   # "what does it look like"
    r"เจ้า\s*คิดว่า.*(?:เมือง|โลก|ที่นี่).*(?:เป็น|ยังไง)",
    r"what do you see",
    r"what(?:'s| is) (?:in|inside|out there|outside|in the)",
    r"who(?:'s| is) (?:there|waiting|in the)",
    r"what is (?:this|the) (?:town|place|room|city) (?:called|like)",
]
_WORLD_RE = re.compile("|".join(_WORLD_AUTHORING), re.IGNORECASE)

# A safe fallback decision prompt (the world is framed; the CHARACTER decides).
_SAFE_PROMPT = "จะทำอะไรต่อ?"


def is_world_authoring_question(text: str) -> bool:
    return bool(_WORLD_RE.search(text or ""))


def screen_decision_prompt(prompt: str | None, actor_name: str | None = None) -> str | None:
    """A decision prompt may ask the CHARACTER what they do, never the player to
    supply world facts. Rewrite offenders."""
    if not prompt:
        return prompt
    if is_world_authoring_question(prompt):
        who = actor_name or "ตัวละครของเจ้า"
        return f"{who}จะทำอย่างไร?"
    return prompt


def _normalize_for_compare(text: str) -> str:
    """Collapse whitespace and case so a near-identical paragraph compares equal
    regardless of trivial reformatting."""
    norm = unicodedata.normalize("NFC", text or "").casefold()
    return re.sub(r"\s+", " ", norm).strip()


def is_repeat_narration(previous: str | None, current: str, *, threshold: float = 0.92) -> bool:
    """True when `current` narration is (near-)identical to the immediately previous
    one delivered in this scene — the "the DM just re-said the last paragraph" failure.

    Deterministic: an exact match after normalization, or a similarity ratio at/above
    `threshold`. The threshold is deliberately high so genuinely new prose that merely
    reuses a name or place is never suppressed; only a substantive repeat is."""
    cur = _normalize_for_compare(current)
    prev = _normalize_for_compare(previous or "")
    if not cur or not prev:
        return False
    if cur == prev:
        return True
    return SequenceMatcher(None, prev, cur).ratio() >= threshold


def screen_narration(text: str, actor_name: str | None = None) -> tuple[str, bool]:
    """Strip world-authoring questions from narration prose. Returns (text, changed).
    A sentence that asks the player to invent scenery is removed; if that empties the
    text, a safe prompt is substituted."""
    if not text:
        return text, False
    kept, changed = [], False
    for line in text.split("\n"):
        # Split a line into sentence-ish chunks on Thai/Latin terminators.
        offending = any(is_world_authoring_question(chunk)
                        for chunk in re.split(r"(?<=[?？])\s*", line) if chunk.strip())
        if offending and "?" in line:
            changed = True
            continue
        kept.append(line)
    result = "\n".join(l for l in kept if l is not None).strip()
    if not result:
        who = actor_name or "ตัวละครของเจ้า"
        result = f"{who}จะทำอย่างไร?"
    return result, changed
