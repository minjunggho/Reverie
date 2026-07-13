"""Character identity — structure without erasure (Phase 2).

Character creation preserves TWO things: the complete player-authored text
(`origin_text`, verbatim) and a structured identity extracted from it. This module
owns the structured side:

- `IDENTITY_FIELDS`: the full field set the conversation may populate.
- Custom-ancestry handling: a stated ancestry outside the bundled species is kept
  as NARRATIVE (appearance/culture) and paired with an owner-approved MECHANICAL
  base package — narrative wings never silently grant flight.
- Unsupported-class handling: a stated class Reverie can't yet run mechanically
  (paladin, sorcerer, …) is preserved in the fiction and mapped to the closest
  supported chassis, which the player still explicitly chooses.
- Evolution seeds: reviewable story proposals generated from the identity, stored
  PENDING until campaign context validates them. They are never guaranteed true
  and never major canon.

Nothing here invents mechanics or grants powers; it proposes, and the player/owner
decide.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# The structured identity the guided conversation may fill in. Every field is
# optional — the player writes what they care about; we never demand a form.
IDENTITY_FIELDS: tuple[str, ...] = (
    # core
    "name", "pronouns", "ancestry", "class_intention", "subclass_intention", "age",
    # appearance
    "appearance", "height_build", "face", "eyes", "hair", "skin",
    "clothing", "distinctive_marks", "voice", "mannerisms",
    # origin / culture
    "culture", "homeland", "religion", "social_class",
    # relationships
    "family", "friends", "mentors", "rivals", "connections",
    # history / psyche
    "past_events", "trauma", "goals", "fears", "ideals", "bonds", "flaws", "secrets",
    # drives
    "reason_for_adventuring", "short_term_goal", "long_term_goal", "boundaries",
)

# Legacy Stage-A hook keys, kept so older drafts and the existing reveal card keep
# working. New extraction writes the full IDENTITY_FIELDS set as well.
LEGACY_HOOK_KEYS: tuple[str, ...] = (
    "concept", "origin", "desire", "fear", "flaw", "connection", "appearance", "name",
)

# The six species the engine runs mechanically today. A stated ancestry outside
# this set is treated as custom (narrative + approved base package).
BUNDLED_SPECIES: frozenset[str] = frozenset({
    "human", "elf", "dwarf", "halfling", "gnome", "orc",
    "tiefling", "dragonborn", "goliath", "aasimar",
})

# Real D&D classes the engine does NOT yet run mechanically. Stating one is fine —
# the fiction is preserved and the closest supported chassis is proposed.
UNSUPPORTED_CLASSES: frozenset[str] = frozenset({
    "paladin", "sorcerer", "warlock", "barbarian", "druid", "monk", "artificer",
})

# Closest supported mechanical chassis for an unsupported stated class. Transparent,
# engine-side; the player still chooses in Stage B (this only orders the pitch).
CHASSIS_FOR_UNSUPPORTED: dict[str, str] = {
    "paladin": "fighter",     # armored oathbound warrior
    "barbarian": "fighter",   # martial powerhouse
    "monk": "fighter",        # martial (unarmed flavor narrative-only for now)
    "sorcerer": "wizard",     # arcane caster
    "warlock": "wizard",      # arcane caster
    "artificer": "wizard",    # arcane caster
    "druid": "cleric",        # nature/divine caster
}

# English → canonical class key, so "I'm a Paladin" / "พาลาดิน" both resolve.
_CLASS_ALIASES: dict[str, str] = {
    "fighter": "fighter", "นักรบ": "fighter",
    "rogue": "rogue", "นักย่องเบา": "rogue", "โจร": "rogue",
    "wizard": "wizard", "จอมเวท": "wizard", "เมจ": "wizard",
    "cleric": "cleric", "นักบวช": "cleric", "พระ": "cleric",
    "ranger": "ranger", "นายพราน": "ranger", "พราน": "ranger",
    "bard": "bard", "กวี": "bard",
    "paladin": "paladin", "พาลาดิน": "paladin", "อัศวิน": "paladin",
    "sorcerer": "sorcerer", "ซอร์เซอเรอร์": "sorcerer",
    "warlock": "warlock", "วอร์ล็อค": "warlock",
    "barbarian": "barbarian", "บาร์บาเรียน": "barbarian",
    "druid": "druid", "ดรูอิด": "druid",
    "monk": "monk", "มังก์": "monk", "นักพรต": "monk",
    "artificer": "artificer",
}

_SPECIES_ALIASES: dict[str, str] = {
    "human": "human", "มนุษย์": "human", "คน": "human",
    "elf": "elf", "เอลฟ์": "elf",
    "dwarf": "dwarf", "แคระ": "dwarf", "คนแคระ": "dwarf",
    "halfling": "halfling", "ฮาล์ฟลิง": "halfling",
    "gnome": "gnome", "โนม": "gnome",
    "orc": "orc", "ออร์ค": "orc",
    "tiefling": "tiefling", "ทีฟลิง": "tiefling",
    "dragonborn": "dragonborn", "ดราก้อนบอร์น": "dragonborn", "มังกร": "dragonborn",
    "goliath": "goliath", "โกไลแอธ": "goliath",
    "aasimar": "aasimar", "อาซิมาร์": "aasimar",
}


def normalize_class_intention(text: str) -> str | None:
    """Map a stated class (Thai/English, any case) to a canonical class key, or
    None if no class is clearly named. Returns keys that may be UNSUPPORTED."""
    low = (text or "").lower()
    for alias, key in sorted(_CLASS_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in low:
            return key
    return None


def normalize_species_intention(text: str) -> str | None:
    """Map a stated ancestry to a bundled species key, or None (which may mean a
    CUSTOM ancestry — the caller decides via `is_custom_ancestry`)."""
    low = (text or "").lower()
    for alias, key in sorted(_SPECIES_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in low:
            return key
    return None


@dataclass
class ClassResolution:
    """How a stated class maps onto what the engine can run."""
    stated: str | None          # canonical class the player named (may be unsupported)
    supported: str | None       # a fully-supported class, if the stated one is
    chassis: str | None         # proposed supported chassis when stated is unsupported
    is_supported: bool
    is_unsupported: bool

    @property
    def recommended(self) -> str | None:
        """The supported class to put first in Stage B (stated if supported, else
        the proposed chassis)."""
        return self.supported or self.chassis


def resolve_class_intention(text: str) -> ClassResolution:
    from app.tabletop.rules.core import SUPPORTED_CLASSES

    stated = normalize_class_intention(text)
    if stated is None:
        return ClassResolution(None, None, None, False, False)
    if stated in SUPPORTED_CLASSES:
        return ClassResolution(stated, stated, None, True, False)
    chassis = CHASSIS_FOR_UNSUPPORTED.get(stated)
    return ClassResolution(stated, None, chassis, False, True)


def is_custom_ancestry(text: str) -> bool:
    """True when a stated ancestry names something real but outside the bundled
    species (Catfolk, winged Dragonborn variant, etc.) — narrative, needs a base
    package. An empty/unclear statement is NOT custom."""
    if not (text or "").strip():
        return False
    return normalize_species_intention(text) is None


# Ancestry keyword → suggested bundled base package for a custom ancestry. The
# mechanical package is chosen explicitly by the player/owner; this only orders it.
_CUSTOM_BASE_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("cat", "แมว", "feline", "beast", "สัตว์"), "halfling"),   # small/agile
    (("wing", "ปีก", "feather", "bird", "นก"), "aasimar"),      # celestial-adjacent
    (("dragon", "มังกร", "scale", "เกล็ด"), "dragonborn"),
    (("giant", "ยักษ์", "large", "ใหญ่"), "goliath"),
    (("fiend", "ปีศาจ", "demon", "horn", "เขา"), "tiefling"),
]


def suggested_base_for_custom(ancestry_text: str) -> str:
    low = (ancestry_text or "").lower()
    for keys, base in _CUSTOM_BASE_HINTS:
        if any(k in low for k in keys):
            return base
    return "human"


@dataclass
class Seed:
    """One reviewable evolution seed. `status` stays 'proposed' until campaign
    context validates it — it is never guaranteed true and never major canon."""
    kind: str          # hook | relationship | rumor | object | connection
    text: str
    status: str = "proposed"

    def as_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text, "status": self.status}


def generate_seeds(identity: dict) -> list[Seed]:
    """Derive up to five reviewable story seeds from the identity — one personal
    hook, one unresolved relationship, one backstory-linked rumor, one personal
    object, one possible campaign connection. Deterministic from what the player
    supplied (so nothing is invented from nothing); empty when there's no material.
    """
    seeds: list[Seed] = []

    def first(*keys: str) -> str:
        for k in keys:
            v = (identity.get(k) or "").strip()
            if v:
                return v
        return ""

    hook = first("short_term_goal", "goals", "desire", "reason_for_adventuring")
    if hook:
        seeds.append(Seed("hook", f"เป้าหมายเฉพาะตัว: {hook}"))

    rel = first("rivals", "mentors", "family", "friends", "connections", "connection")
    if rel:
        seeds.append(Seed("relationship", f"ความสัมพันธ์ที่ยังค้างคา: {rel}"))

    rumor_src = first("secrets", "trauma", "past_events", "origin")
    if rumor_src:
        seeds.append(Seed("rumor", f"ข่าวลือที่โยงกับอดีต: {rumor_src} — จริงเท็จยังไม่ยืนยัน"))

    obj = first("distinctive_marks", "keepsake", "clothing")
    if obj:
        seeds.append(Seed("object", f"ของติดตัวที่มีความหมาย: {obj}"))

    conn = first("homeland", "culture", "religion", "faction")
    if conn:
        seeds.append(Seed("connection", f"จุดเชื่อมกับโลกของแคมเปญ: {conn}"))

    return seeds[:5]


def merge_identity(existing: dict, updates: dict) -> dict:
    """Fold newly-extracted fields into the identity without dropping anything the
    player already gave. Non-empty updates win; empty updates never erase."""
    merged = dict(existing or {})
    for key, value in (updates or {}).items():
        if isinstance(value, str):
            value = value.strip()
        if value:
            merged[key] = value
    return merged
