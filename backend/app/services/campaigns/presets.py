"""Mechanical presets for the supported classes (single source of truth).

Used by both the quick path (`!rv character <name> <class>`) and the guided
creation flow. The AI maps a player's *fantasy* onto one of these presets; it never
invents mechanics.
"""
from __future__ import annotations

CLASS_PRESETS: dict[str, dict] = {
    "fighter": dict(abilities={"str": 16, "con": 15, "dex": 13, "wis": 12, "int": 10, "cha": 8},
                    proficiencies=["athletics", "intimidation"], max_hp=12, ac=16),
    "rogue": dict(abilities={"dex": 16, "int": 13, "wis": 12, "con": 12, "cha": 10, "str": 8},
                  proficiencies=["stealth", "perception", "sleight_of_hand", "acrobatics"],
                  max_hp=9, ac=14),
    "wizard": dict(abilities={"int": 16, "con": 13, "dex": 12, "wis": 12, "cha": 10, "str": 8},
                   proficiencies=["arcana", "investigation"], max_hp=7, ac=12),
    "cleric": dict(abilities={"wis": 16, "con": 14, "str": 13, "cha": 12, "dex": 10, "int": 8},
                   proficiencies=["medicine", "insight", "religion"], max_hp=10, ac=15),
    "ranger": dict(abilities={"dex": 16, "wis": 14, "con": 13, "str": 12, "int": 10, "cha": 8},
                   proficiencies=["survival", "perception", "stealth", "animal_handling"],
                   max_hp=11, ac=14),
    "bard": dict(abilities={"cha": 16, "dex": 14, "con": 13, "int": 12, "wis": 10, "str": 8},
                 proficiencies=["persuasion", "performance", "deception", "insight"],
                 max_hp=9, ac=13),
    "sorcerer": dict(abilities={"cha": 16, "con": 14, "dex": 13, "wis": 12, "int": 10, "str": 8},
                     proficiencies=["arcana", "deception"], max_hp=7, ac=12),
    "warlock": dict(abilities={"cha": 16, "con": 14, "dex": 13, "wis": 12, "int": 10, "str": 8},
                    proficiencies=["arcana", "intimidation"], max_hp=9, ac=12),
    "barbarian": dict(abilities={"str": 16, "con": 15, "dex": 14, "wis": 12, "cha": 10, "int": 8},
                      proficiencies=["athletics", "intimidation"], max_hp=14, ac=15),
    "monk": dict(abilities={"dex": 16, "wis": 15, "con": 13, "str": 12, "int": 10, "cha": 8},
                 proficiencies=["acrobatics", "stealth"], max_hp=9, ac=15),
}

# Thai class fantasy names for reveals/prompts.
CLASS_TH: dict[str, str] = {
    "fighter": "นักรบ", "rogue": "นักย่องเบา", "wizard": "จอมเวท",
    "cleric": "นักบวช", "ranger": "นายพราน", "bard": "กวี",
    "sorcerer": "จอมเวทสายเลือด", "warlock": "ผู้สืบสัญญา",
    "barbarian": "นักรบคลั่ง", "monk": "นักพรตหมัด",
}

# Deterministic fallback when the AI proposes an unsupported class: map concept
# keywords to the closest supported fantasy. Engine-side judgement, transparent.
_CLASS_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("rogue", ("มีด", "ลอบ", "ขโมย", "โจร", "เงียบ", "ย่อง", "นักล้วง")),
    ("wizard", ("เวท", "คาถา", "หนังสือ", "ตำรา", "เมจ", "วิชา")),
    ("cleric", ("บวช", "ศรัทธา", "เทพ", "รักษา", "สวด", "พระ")),
    ("ranger", ("ธนู", "ป่า", "ล่า", "สัตว์", "ตามรอย", "พราน")),
    ("bard", ("เพลง", "ดนตรี", "พิณ", "เล่าเรื่อง", "เจรจา", "โกหก", "หลอก")),
    ("fighter", ("ดาบ", "นักรบ", "ทหาร", "สู้", "โล่", "กองทัพ")),
]


def infer_class_from_concept(text: str) -> str:
    t = text or ""
    for cls, words in _CLASS_KEYWORDS:
        if any(w in t for w in words):
            return cls
    return "fighter"
