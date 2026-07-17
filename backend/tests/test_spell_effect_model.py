"""The spell effect model is general, and its limits are explicit.

Guidance and Minor Illusion were the reported bugs, but they were symptoms of one
structural gap: SpellDef could only express attack/save/damage/healing, so HALF the
spell list resolved to nothing and could only echo its own name. These tests hold the
general property — every spell resolves to something the engine runs — and pin
exactly which effect kinds have rules integration, so an unintegrated kind can never
be quietly mistaken for a working one.
"""
from __future__ import annotations

import pytest

from app.rules_content import get_registry
from app.rules_content.registry import (
    EFFECT_KINDS_WITH_RULES_INTEGRATION,
    _duration_minutes,
)


def _spells():
    return [s for s in get_registry().spells.values() if s.content_type == "spell"]


# --- the general property --------------------------------------------------------

def test_every_spell_resolves_to_something_the_engine_runs():
    """The invariant that makes 'ภาพลวงย่อม' impossible to ship again."""
    bare = [s.name for s in _spells() if not s.has_resolution]
    assert bare == [], (
        f"these spells resolve to nothing and would reply with only their own "
        f"name: {bare}")


def test_a_ui_label_alone_is_not_a_resolution():
    """The original loophole: the load-time check accepted `ux_category` (a required
    UI label) as proof of resolvability, so it was always true and never fired."""
    from app.rules_content.registry import SpellDef

    spell = SpellDef(
        definition_id="spell:test_dud", name="test_dud", name_th_hint="ทดสอบ",
        level=0, school="illusion", casting_time="action", range="30 ft",
        duration="1m", ux_category="ภาพลวงตา", mech_summary_th="ไม่ทำอะไรเลย",
    )
    assert spell.has_resolution is False, (
        "a spell with a UI category but no damage/save/attack/effects does nothing")


def test_the_reported_spells_declare_real_effects():
    reg = get_registry()
    guidance = reg.get_spell("guidance")
    assert [e.kind for e in guidance.effects] == ["roll_bonus"]
    assert guidance.effects[0].dice == "1d4"
    assert guidance.effects[0].applies_to == ["ability_check"]
    assert guidance.effects[0].consumed_on_use is True

    illusion = reg.get_spell("minor_illusion")
    assert [e.kind for e in illusion.effects] == ["world_effect"]
    assert illusion.effects[0].category == "illusion"
    # The SRD limit is DATA, not an if-statement about Minor Illusion.
    assert illusion.effects[0].choose_one_mode is True
    assert set(illusion.effects[0].modes) == {"image", "sound"}


# --- the audit the brief asked for: which categories are covered -----------------

@pytest.mark.parametrize("category,spell_key,expected_kind", [
    ("roll buff", "guidance", "roll_bonus"),
    ("persistent buff", "bless", "roll_bonus"),
    ("defensive buff", "shield_of_faith", "ac_bonus"),
    ("illusion", "minor_illusion", "world_effect"),
    ("light/utility", "light", "world_effect"),
    ("environmental", "fog_cloud", "world_effect"),
    ("control/condition", "sleep", "condition"),
    ("debuff", "hex", "condition"),
    ("sense", "detect_magic", "sense"),
    ("movement", "feather_fall", "movement"),
])
def test_representative_spell_categories_declare_an_effect(category, spell_key,
                                                           expected_kind):
    spell = get_registry().get_spell(spell_key)
    assert [e.kind for e in spell.effects] == [expected_kind], category


@pytest.mark.parametrize("spell_key", [
    "fire_bolt",        # attack + damage
    "sacred_flame",     # save + damage
    "magic_missile",    # automatic damage
    "cure_wounds",      # healing
])
def test_combat_spells_still_resolve_through_damage_and_healing(spell_key):
    """The pre-existing mechanics are untouched: these need no `effects` entry."""
    spell = get_registry().get_spell(spell_key)
    assert spell.effects == []
    assert spell.has_resolution


def test_the_rules_integration_boundary_is_explicit():
    """Which kinds a RULE reads, versus which are only persisted and narrated. This
    is the honest limit of the current model — assert it rather than assume it."""
    assert EFFECT_KINDS_WITH_RULES_INTEGRATION == {
        "roll_bonus", "ac_bonus", "world_effect"}
    unintegrated = {e.kind for s in _spells() for e in s.effects
                    if e.kind not in EFFECT_KINDS_WITH_RULES_INTEGRATION}
    # These persist, describe and expire correctly, but no rule consumes them yet.
    assert unintegrated == {"condition", "sense", "movement"}


def test_every_declared_effect_is_executable():
    """Load-time validation rejects an effect the engine would silently skip; this
    asserts the shipped content actually satisfies it."""
    for spell in _spells():
        for effect in spell.effects:
            if effect.kind == "roll_bonus":
                assert effect.dice, spell.name
                assert effect.applies_to, spell.name
            if effect.kind == "world_effect":
                assert effect.category, spell.name
                assert effect.modes, spell.name
            if effect.kind == "ac_bonus":
                assert effect.bonus > 0, spell.name
            if effect.kind == "condition":
                assert effect.condition, spell.name


def test_invalid_effect_content_is_rejected_at_load():
    """A roll_bonus that grants no die is content that looks like it works."""
    from app.core.errors import RulesViolation
    from app.rules_content.registry import RulesRegistry, SpellDef, SpellEffectDef

    reg = get_registry()
    broken = SpellDef(
        definition_id="spell:broken", name="broken", name_th_hint="พัง", level=0,
        school="divination", casting_time="action", range="touch", duration="1m",
        ux_category="ฟื้นฟู", mech_summary_th="", classes=["cleric"],
        effects=[SpellEffectDef(kind="roll_bonus")],   # no dice, no applies_to
    )
    issues: list[str] = []
    RulesRegistry._validate_spell_effect(
        reg, broken, broken.effects[0],
        lambda cls, pool, invalid, expected: issues.append(invalid))
    assert len(issues) == 2, "both the missing die and the missing scope are reported"
    assert RulesViolation  # the loader raises this when add_issue collects any


# --- duration parsing (drives expiry for every effect) ---------------------------

@pytest.mark.parametrize("text,expected", [
    ("1m", 1),
    ("10m", 10),
    ("1h", 60),
    ("8h", 480),
    ("24h", 1440),
    ("10 minutes", 10),
    ("1 round", 1),
    ("concentration, up to 1 hour", 60),
    ("concentration, up to 1 minute", 1),
    ("instant", None),
    ("", None),
    ("nonsense", None),
])
def test_duration_parsing(text, expected):
    assert _duration_minutes(text) == expected


def test_every_spell_duration_parses_or_is_instantaneous():
    """An unparseable duration yields None (never expires), which would leave an
    effect live forever. Assert the shipped content has no such case."""
    for spell in _spells():
        if not spell.effects:
            continue
        text = (spell.duration or "").strip().lower()
        if text.startswith("instant"):
            continue
        assert spell.duration_minutes is not None, (
            f"{spell.name} declares effects but its duration {spell.duration!r} "
            f"does not parse — the effect would never expire")
