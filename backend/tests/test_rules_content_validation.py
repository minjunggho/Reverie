from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.errors import RulesViolation
from app.rules_content import (
    ChoiceOption,
    RulesRegistry,
    normalize_choice_name,
    resolve_choice_name,
)

_CONTENT_DIR = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "rules_content"
    / "srd_5_2_1"
)


def _copy_content(tmp_path: Path) -> Path:
    target = tmp_path / "rules"
    shutil.copytree(_CONTENT_DIR, target)
    return target


def _read(path: Path, name: str):
    return json.loads((path / name).read_text(encoding="utf-8"))


def _write(path: Path, name: str, value) -> None:
    (path / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "typed",
    [
        "Shield of Faith",
        "shield_of_faith",
        "shield-of-faith",
        "SHIELD OF FAITH",
        " shield   of   faith ",
    ],
)
def test_spell_choice_resolves_english_key_and_separator_forms(typed: str):
    result = RulesRegistry().resolve_spell_name(
        typed,
        allowed_keys=["shield_of_faith"],
    )
    assert result.key == "shield_of_faith"
    assert not result.ambiguous


def test_spell_choice_resolves_thai_display_name_and_configured_alias():
    registry = RulesRegistry()
    allowed = ["shield_of_faith"]

    assert registry.resolve_spell_name(
        "โล่แห่งศรัทธา", allowed_keys=allowed
    ).key == "shield_of_faith"
    assert registry.resolve_spell_name(
        "faith shield", allowed_keys=allowed
    ).key == "shield_of_faith"
    assert registry.resolve_spell_name(
        "Hunter's Mark", allowed_keys=["hunters_mark"]
    ).key == "hunters_mark"


def test_spell_choice_no_match_returns_suggestions_without_partial_selection():
    registry = RulesRegistry()
    result = registry.resolve_spell_name(
        "shild of faith",
        allowed_keys=["shield_of_faith", "shield"],
    )
    assert result.key is None
    assert result.suggestion_keys[0] == "shield_of_faith"

    partial = registry.resolve_spell_name(
        "shield",
        allowed_keys=["shield_of_faith"],
    )
    assert partial.key is None


def test_ambiguous_normalized_alias_never_silently_selects():
    result = resolve_choice_name(
        " shared--alias ",
        [
            ChoiceOption("first_spell", ("shared alias",)),
            ChoiceOption("second_spell", ("shared_alias",)),
        ],
    )
    assert result.key is None
    assert result.ambiguous_keys == ("first_spell", "second_spell")


def test_choice_normalization_is_unicode_safe():
    assert normalize_choice_name(" ＳＨＩＥＬＤ—ＯＦ＿ＦＡＩＴＨ ") == "shield of faith"


def test_valid_rules_content_passes_and_exposes_only_backend_supported_classes():
    registry = RulesRegistry()
    assert set(registry.selectable_classes) == {
        "fighter", "rogue", "wizard", "cleric", "ranger", "bard", "sorcerer", "warlock",
        "barbarian", "monk",
    }
    assert [cls.name for cls in registry.selectable_class_defs()] == list(
        registry.selectable_classes
    )
    assert "lay_on_hands" not in registry.spells


def test_every_class_declares_an_explicit_support_status():
    """Selectable ⇔ FULLY_SUPPORTED; unfinished classes stay in the pack as
    UNSUPPORTED (retained content, never offered as playable)."""
    registry = RulesRegistry()
    statuses = {name: cls.support_status for name, cls in registry.classes.items()}
    assert set(statuses.values()) <= {
        "FULLY_SUPPORTED", "PARTIALLY_SUPPORTED", "UNSUPPORTED"
    }
    fully = {n for n, s in statuses.items() if s == "FULLY_SUPPORTED"}
    assert fully == set(registry.selectable_classes)
    # Still-unfinished classes are retained, not deleted, and not FULLY_SUPPORTED.
    assert {"druid", "paladin"} <= set(statuses)
    assert all(statuses[n] != "FULLY_SUPPORTED"
               for n in ("druid", "paladin"))


def test_fully_supported_flag_on_unselectable_class_fails(tmp_path: Path):
    content = _copy_content(tmp_path)
    classes = _read(content, "classes.json")
    for cls in classes:
        if cls["name"] == "paladin":          # a still-locked class
            cls["support_status"] = "FULLY_SUPPORTED"
    _write(content, "classes.json", classes)

    with pytest.raises(RulesViolation) as exc_info:
        RulesRegistry(content)

    message = str(exc_info.value)
    assert "pool=support_status" in message
    assert "paladin" in message


def test_selectable_class_without_rules_content_fails(tmp_path: Path):
    content = _copy_content(tmp_path)
    classes = _read(content, "classes.json")
    classes = [cls for cls in classes if cls["name"] != "fighter"]
    _write(content, "classes.json", classes)

    with pytest.raises(RulesViolation) as exc_info:
        RulesRegistry(content)

    message = str(exc_info.value)
    assert "class=fighter" in message
    assert "pool=class" in message
    assert "expected every selectable class to have a ClassDef" in message
    assert "rules_content_version=1" in message


def test_empty_required_cantrip_pool_fails(tmp_path: Path):
    content = _copy_content(tmp_path)
    spells = _read(content, "spells.json")
    for spell in spells:
        if spell["level"] == 0:
            spell["classes"] = [name for name in spell["classes"] if name != "wizard"]
    _write(content, "spells.json", spells)

    with pytest.raises(RulesViolation, match=r"class=wizard; pool=cantrips; invalid=legal count 0"):
        RulesRegistry(content)


def test_empty_required_prepared_spell_pool_fails(tmp_path: Path):
    content = _copy_content(tmp_path)
    spells = _read(content, "spells.json")
    for spell in spells:
        if spell["level"] == 1:
            spell["classes"] = [name for name in spell["classes"] if name != "wizard"]
    _write(content, "spells.json", spells)

    with pytest.raises(
        RulesViolation,
        match=r"class=wizard; pool=prepared_spells; invalid=legal count 0",
    ):
        RulesRegistry(content)


def test_insufficient_legal_choices_fails(tmp_path: Path):
    content = _copy_content(tmp_path)
    spells = _read(content, "spells.json")
    wizard_cantrips = [spell for spell in spells
                       if spell["level"] == 0 and "wizard" in spell["classes"]]
    keep = {spell["name"] for spell in wizard_cantrips[:2]}
    for spell in wizard_cantrips:
        if spell["name"] not in keep:
            spell["classes"].remove("wizard")
    _write(content, "spells.json", spells)

    with pytest.raises(
        RulesViolation,
        match=r"class=wizard; pool=cantrips; invalid=required count 3, legal count 2",
    ):
        RulesRegistry(content)


def test_duplicate_normalized_canonical_spell_key_fails(tmp_path: Path):
    content = _copy_content(tmp_path)
    spells = _read(content, "spells.json")
    duplicate = dict(next(spell for spell in spells if spell["name"] == "shield_of_faith"))
    duplicate["definition_id"] = "spell:duplicate_shield_of_faith"
    duplicate["name"] = "shield-of-faith"
    spells.append(duplicate)
    _write(content, "spells.json", spells)

    with pytest.raises(RulesViolation, match=r"pool=spells; invalid=duplicate canonical key"):
        RulesRegistry(content)


def test_ambiguous_normalized_spell_alias_fails(tmp_path: Path):
    content = _copy_content(tmp_path)
    spells = _read(content, "spells.json")
    spells[0]["aliases"] = ["shared alias"]
    spells[1]["aliases"] = ["shared-alias"]
    _write(content, "spells.json", spells)

    with pytest.raises(
        RulesViolation,
        match=r"pool=spell_aliases; invalid=ambiguous normalized alias 'shared alias'",
    ):
        RulesRegistry(content)


def test_class_feature_in_spell_pool_fails_and_lay_on_hands_is_rejected(tmp_path: Path):
    content = _copy_content(tmp_path)
    spells = _read(content, "spells.json")
    spells.append({
        "definition_id": "spell:lay_on_hands",
        "name": "lay_on_hands",
        "name_th_hint": "วางมือรักษา",
        "level": 1,
        "school": "abjuration",
        "casting_time": "action",
        "range": "touch",
        "duration": "instant",
        "ux_category": "ฟื้นฟู",
        "mech_summary_th": "ความสามารถประจำคลาส ไม่ใช่คาถา",
        "classes": ["paladin"],
    })
    _write(content, "spells.json", spells)

    with pytest.raises(
        RulesViolation,
        match=r"class=paladin; pool=level_1_spells; invalid=key 'lay_on_hands'",
    ):
        RulesRegistry(content)


def test_spell_displayed_for_illegal_class_fails(tmp_path: Path):
    content = _copy_content(tmp_path)
    spells = _read(content, "spells.json")
    shield = next(spell for spell in spells if spell["name"] == "shield_of_faith")
    shield["display_classes"] = ["wizard"]
    _write(content, "spells.json", spells)

    with pytest.raises(
        RulesViolation,
        match=r"class=wizard; pool=displayed_spells; invalid=spell key 'shield_of_faith'",
    ):
        RulesRegistry(content)


def test_spell_cannot_claim_a_non_spellcasting_class_pool(tmp_path: Path):
    content = _copy_content(tmp_path)
    spells = _read(content, "spells.json")
    shield = next(spell for spell in spells if spell["name"] == "shield_of_faith")
    shield["classes"].append("fighter")
    _write(content, "spells.json", spells)

    with pytest.raises(
        RulesViolation,
        match=(r"class=fighter; pool=spell.classes; invalid=spell key "
               r"'shield_of_faith'.*expected every class listed for a spell"),
    ):
        RulesRegistry(content)


def test_ui_backend_rules_content_version_mismatch_fails(tmp_path: Path):
    content = _copy_content(tmp_path)
    manifest = _read(content, "manifest.json")
    manifest["ui_rules_content_version"] = "2"
    _write(content, "manifest.json", manifest)

    with pytest.raises(
        RulesViolation,
        match=r"pool=ui_version; invalid=UI version '2'.*rules_content_version=1",
    ):
        RulesRegistry(content)


async def test_application_startup_runs_rules_content_validation(monkeypatch):
    import app.main as main_module

    def fail_validation():
        raise RulesViolation("invalid startup rules content")

    monkeypatch.setattr(main_module, "get_registry", fail_validation)
    with pytest.raises(RulesViolation, match="invalid startup rules content"):
        async with main_module.lifespan(SimpleNamespace()):
            pass
