"""Light, deterministic structured-intent extraction from raw player text.

This is a fast first pass, NOT the authoritative interpreter: it pulls out quoted
dialogue, a leading trigger/condition, a declared fallback, and a movement signal so the
planning UI and the resolver have structure to work with immediately. The real
`ActionInterpreter` (LLM) can enrich a frozen submission during resolution; the player's
`raw_player_text` is always preserved verbatim for narration regardless.
"""
from __future__ import annotations

import re

# Quoted dialogue: straight or Thai/curly quotes.
_QUOTE_RE = re.compile(r"[\"“]([^\"”]+)[\"”]")
# A leading condition/trigger clause: "When/If/เมื่อ/ถ้า/ตอนที่ …," up to the first comma.
_CONDITION_RE = re.compile(
    r"^\s*(?:when|if|เมื่อ|ถ้า|ตอนที่|หาก)\b(.+?),", re.IGNORECASE)
# A declared fallback: "otherwise/instead/else/ไม่งั้น/มิฉะนั้น … (instead)".
_FALLBACK_RE = re.compile(
    r"(?:otherwise|instead|else if|ไม่งั้น|มิฉะนั้น|ถ้าไม่ได้|ถ้าไม่มี)\b(.+)$", re.IGNORECASE)
_MOVE_WORDS = ("move", "step", "walk", "run", "crawl", "climb", "เดิน", "ขยับ",
               "วิ่ง", "คลาน", "ปีน", "ย่อง", "ถอย", "เข้าไป", "ออกไป")


def parse_submission(raw_text: str) -> dict:
    """Best-effort structured fields from raw text. Empty/unknown fields are omitted so
    an explicit UI-supplied value is never overwritten by a blank guess."""
    text = (raw_text or "").strip()
    out: dict = {}
    if not text:
        return out

    dialogue = " / ".join(m.group(1).strip() for m in _QUOTE_RE.finditer(text))
    if dialogue:
        out["dialogue"] = dialogue

    cond = _CONDITION_RE.search(text)
    if cond:
        out["condition"] = cond.group(1).strip()

    fb = _FALLBACK_RE.search(text)
    if fb:
        out["fallback_action"] = fb.group(1).strip(" .")

    low = text.lower()
    if any(w in low for w in _MOVE_WORDS):
        out["movement_intent"] = True

    # Primary action = the sentence with dialogue and any declared fallback removed, so
    # "I move between them and strike" survives as the action rather than the flavour.
    primary = _QUOTE_RE.sub("", text)
    if fb:
        primary = primary[: fb.start()]
    out["primary_action"] = primary.strip(" ,.")
    return out
