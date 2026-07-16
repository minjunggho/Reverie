"""Anti-hallucination / agency screen for narrator output.

In AUTHORITATIVE_WORLD mode, Reverie must never ask a player to invent an objective
world fact ("เจ้าเห็นอะไรข้างนอก?") or decide what a DM-owned NPC/enemy will do
("Oruktyr จะเสนออะไร?"). Offending prompts are deterministically rewritten to an
agency-safe question about the acting player character.

Questions about the player's OWN character remain allowed. This is a structural guard,
not a prompt plea — see docs/world-canon.md and issue #1.
"""
from __future__ import annotations

import re

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

# Questions that hand control of an NPC, enemy, creature, or other DM-owned entity to
# the player. Actor-owned questions are explicitly exempted by `_actor_owns_question`.
_DM_AGENCY = [
    r"(?:จะ|ควรจะ).*(?:ตอบ|พูด|บอก|แนะนำ|เสนอ|อธิบาย|ทำ|เลือก|ตัดสินใจ|โจมตี|ช่วย|ไป)"
    r".*(?:อะไร|อย่างไร|ยังไง|แบบไหน|ทางไหน)",
    r"(?:ตอบ|พูด|บอก|แนะนำ|เสนอ|ทำ|เลือก|ตัดสินใจ)\s*(?:อะไร|อย่างไร|ยังไง|แบบไหน)",
    r"what will .+ (?:do|say|answer|suggest|choose|decide)",
    r"how will .+ (?:respond|answer|react|decide)",
    r"what should .+ (?:do|say|answer|suggest|choose)",
]
_DM_AGENCY_RE = re.compile("|".join(_DM_AGENCY), re.IGNORECASE)

# A safe fallback decision prompt (the world is framed; the CHARACTER decides).
_SAFE_PROMPT = "จะทำอะไรต่อ?"


def is_world_authoring_question(text: str) -> bool:
    return bool(_WORLD_RE.search(text or ""))


def _actor_owns_question(text: str, actor_name: str | None) -> bool:
    """Return True when the grammatical decision owner is the acting PC.

    Merely mentioning the actor is not enough: "Oruktyr จะตอบ Veskan อย่างไร?"
    contains Veskan but still delegates Oruktyr's agency. The actor's name must appear
    immediately before a player-choice verb.
    """
    if not text or not actor_name:
        return False
    actor = re.escape(actor_name.strip())
    if not actor:
        return False
    pattern = re.compile(
        rf"{actor}\s*(?:จะ|ควรจะ|อยาก|ต้องการ|เลือกจะ|คิดจะ)?\s*"
        rf"(?:ทำ|พูด|ตอบ|ถาม|ตรวจ|สำรวจ|เสี่ยง|เลือก|ตัดสินใจ|ไป|ช่วย|โจมตี)",
        re.IGNORECASE,
    )
    return bool(pattern.search(text))


def is_dm_agency_question(text: str, actor_name: str | None = None) -> bool:
    """Detect a question asking the player to choose a DM-owned entity's action."""
    if not text or _actor_owns_question(text, actor_name):
        return False
    return bool(_DM_AGENCY_RE.search(text))


def is_invalid_decision_question(text: str, actor_name: str | None = None) -> bool:
    return is_world_authoring_question(text) or is_dm_agency_question(text, actor_name)


def screen_decision_prompt(prompt: str | None, actor_name: str | None = None) -> str | None:
    """A decision prompt may ask the CHARACTER what they do, never the player to
    supply world facts or control a DM-owned entity. Rewrite offenders."""
    if not prompt:
        return prompt
    if is_invalid_decision_question(prompt, actor_name):
        who = actor_name or "ตัวละครของเจ้า"
        return f"{who}จะทำอย่างไร?"
    return prompt


def screen_narration(text: str, actor_name: str | None = None) -> tuple[str, bool]:
    """Strip invalid questions from narration prose. Returns (text, changed).

    A sentence that asks the player to invent scenery or control an NPC/enemy is
    removed; if that empties the text, a safe actor-owned prompt is substituted.
    """
    if not text:
        return text, False
    kept, changed = [], False
    for line in text.split("\n"):
        # Split a line into sentence-ish chunks on Thai/Latin terminators.
        offending = any(
            is_invalid_decision_question(chunk, actor_name)
            for chunk in re.split(r"(?<=[?？])\s*", line)
            if chunk.strip()
        )
        if offending and ("?" in line or "？" in line):
            changed = True
            continue
        kept.append(line)
    result = "\n".join(l for l in kept if l is not None).strip()
    if not result:
        who = actor_name or "ตัวละครของเจ้า"
        result = f"{who}จะทำอย่างไร?"
    return result, changed
