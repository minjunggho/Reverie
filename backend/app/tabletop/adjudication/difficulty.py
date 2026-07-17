"""Situational difficulty — a DC that answers "how hard is this, HERE, NOW".

`resolve_dc` maps a band to a rung on a fixed ladder: 5/10/15/20/25/30. That is the
intrinsic difficulty of a TASK, and it is the same number whether the lock is picked
in daylight or in a storm, and whether the innkeeper adores you or is one word from
calling the watch. The band is necessary but not sufficient.

This module composes the DC the table actually rolls against:

    DC = band rung + Σ situational factors, capped and clamped

A factor comes from one of two places, and NEITHER lets the model pick a number:

  ENGINE factors are derived from authoritative state — an NPC's earned feeling
  toward this specific character, their mood, the weather, the scene's tension, and
  the world effects standing in the room (a fog cloud really does make it harder to
  spot someone). Deterministic and reproducible.

  PROPOSED factors come from the adjudicator, but only as KEYS from the closed
  vocabulary below. The engine owns the delta, the skills it applies to, and the cap.
  The model can say "the target is distracted"; it cannot say "that is worth -4".
  A key outside the allowlist is dropped, not honoured.

Every factor is named and explainable, so the resolution can show its reasoning
rather than presenting an unexplained number.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.models.enums import BAND_TO_DC, DifficultyBand

log = get_logger(__name__)

# The DC ladder's rungs are 5 apart, so factors are deliberately small: they should
# shade a difficulty (15 → 17), never leap a whole band on their own.
MAX_TOTAL_SWING = 7
DC_FLOOR, DC_CEILING = 5, 30

_SOCIAL = frozenset({"persuasion", "deception", "intimidation", "performance",
                     "insight"})
_PERCEPTIVE = frozenset({"perception", "investigation", "insight", "survival"})
_PHYSICAL = frozenset({"athletics", "acrobatics", "stealth", "sleight_of_hand"})


@dataclass(frozen=True)
class FactorDef:
    """One thing that can make a check harder or easier. `applies_to=None` means any
    check; otherwise the factor is ignored for skills outside the set — darkness has
    no business modifying a Persuasion roll."""
    key: str
    delta: int                      # + harder, - easier
    label_th: str
    applies_to: frozenset[str] | None = None

    def relevant_to(self, skill: str | None) -> bool:
        if self.applies_to is None:
            return True
        return bool(skill and skill in self.applies_to)


# The CLOSED vocabulary the adjudicator may propose from. Adding an entry here is a
# deliberate rules decision; the model can only choose among them.
SITUATIONAL_FACTORS: dict[str, FactorDef] = {f.key: f for f in (
    # --- attention / awareness of the subject ---------------------------------
    FactorDef("target_distracted", -3, "เป้าหมายกำลังเสียสมาธิ"),
    FactorDef("target_alert", +3, "เป้าหมายกำลังระแวดระวัง"),
    FactorDef("target_impaired", -2, "เป้าหมายมึนเมา/อ่อนล้า"),
    # --- tools and preparation -------------------------------------------------
    FactorDef("proper_tools", -2, "มีเครื่องมือที่เหมาะสม"),
    FactorDef("improvised_tools", +2, "ใช้ของแทนเครื่องมือ"),
    FactorDef("well_prepared", -2, "เตรียมการมาอย่างดี"),
    # --- time ------------------------------------------------------------------
    FactorDef("ample_time", -2, "มีเวลาเหลือเฟือ"),
    FactorDef("time_pressure", +3, "ถูกเวลาบีบ"),
    # --- environment (the parts the schema does not model) ---------------------
    FactorDef("darkness", +3, "มืด", _PERCEPTIVE | _PHYSICAL),
    FactorDef("bright_light", -1, "สว่างจ้า", _PERCEPTIVE),
    FactorDef("loud_noise", +2, "เสียงดังกลบ", _PERCEPTIVE | _SOCIAL),
    FactorDef("silence", +2, "เงียบจนได้ยินทุกเสียง", frozenset({"stealth"})),
    FactorDef("crowded", -2, "ผู้คนพลุกพล่านช่วยกลบ", frozenset({"stealth",
                                                                  "sleight_of_hand"})),
    FactorDef("difficult_terrain", +2, "พื้นที่กีดขวาง", _PHYSICAL),
    FactorDef("favourable_position", -2, "ได้ตำแหน่งได้เปรียบ", _PHYSICAL),
    FactorDef("awkward_position", +2, "อยู่ในท่าที่เสียเปรียบ", _PHYSICAL),
    # --- social specifics ------------------------------------------------------
    FactorDef("public_setting", +2, "อยู่ต่อหน้าผู้คน", _SOCIAL),
    FactorDef("private_setting", -1, "คุยกันตามลำพัง", _SOCIAL),
    FactorDef("language_barrier", +3, "สื่อสารกันไม่คล่อง", _SOCIAL),
    FactorDef("request_against_interest", +3, "ขอในสิ่งที่ขัดผลประโยชน์เขา", _SOCIAL),
    FactorDef("offer_benefits_target", -3, "ข้อเสนอเป็นผลดีกับเขา", _SOCIAL),
    FactorDef("ally_assisting", -2, "มีเพื่อนช่วยเสริม"),
)}


@dataclass
class DCFactor:
    """A factor that actually applied to THIS check."""
    key: str
    delta: int
    label_th: str
    source: str            # "engine" | "proposed"

    def as_dict(self) -> dict:
        return {"key": self.key, "delta": self.delta, "label_th": self.label_th,
                "source": self.source}


@dataclass
class ComposedDC:
    band: DifficultyBand
    base: int
    total: int
    factors: list[DCFactor] = field(default_factory=list)
    capped: bool = False       # the raw swing exceeded MAX_TOTAL_SWING

    @property
    def swing(self) -> int:
        return self.total - self.base

    def explain_th(self) -> str:
        """One readable line: the base, then what moved it and why. Shown with the
        roll so a changed DC never looks arbitrary."""
        if not self.factors:
            return f"DC {self.base} ฐาน"
        bits = [f"DC {self.base} ฐาน"]
        for f in self.factors:
            bits.append(f"{f.delta:+d} {f.label_th}")
        return "  ·  ".join(bits)

    def as_dict(self) -> dict:
        return {"band": str(self.band), "base": self.base, "total": self.total,
                "swing": self.swing, "capped": self.capped,
                "factors": [f.as_dict() for f in self.factors]}


def factors_from_keys(keys: list[str] | None, *, skill: str | None,
                      source: str = "proposed") -> list[DCFactor]:
    """Turn proposed KEYS into factors. A key that is not in the vocabulary is
    dropped — an unknown factor is never guessed at — and one that does not apply to
    this skill is ignored."""
    out: list[DCFactor] = []
    seen: set[str] = set()
    for key in keys or []:
        norm = (key or "").strip().lower()
        definition = SITUATIONAL_FACTORS.get(norm)
        if definition is None:
            log.info("dropped unknown situational factor", extra={"key": key})
            continue
        if norm in seen or not definition.relevant_to(skill):
            continue
        seen.add(norm)
        out.append(DCFactor(key=definition.key, delta=definition.delta,
                            label_th=definition.label_th, source=source))
    return out


def compose_dc(band: DifficultyBand | None, factors: list[DCFactor] | None = None,
               ) -> ComposedDC:
    """The final DC: the band's rung, moved by the factors, capped and clamped.

    The cap matters: a pile of small factors must not silently turn a Medium task
    into a Nearly Impossible one. The band is still the dominant term.
    """
    resolved_band = band or DifficultyBand.MEDIUM
    base = BAND_TO_DC[resolved_band]
    applied = list(factors or [])
    raw = sum(f.delta for f in applied)
    swing = max(-MAX_TOTAL_SWING, min(MAX_TOTAL_SWING, raw))
    total = max(DC_FLOOR, min(DC_CEILING, base + swing))
    return ComposedDC(band=resolved_band, base=base, total=total, factors=applied,
                      capped=raw != swing)
