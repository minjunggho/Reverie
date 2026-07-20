"""Components V2 UI system — payload/rendering, localization, and flow tests.

These exercise the declarative screen model (`app.presentation.screen`/`screens`),
the native V2 renderer (`discord_bot.components_v2`), and the two migrated flows
(deity selection, spell preparation) through the real routing path. Assertions are
semantic (a control's presence, an option default, the re-entry a click produces),
not brittle payload snapshots.
"""
from __future__ import annotations

import asyncio
from itertools import count

import discord
import pytest
from sqlalchemy import select

from app.discord_bridge import AdminBridge, DiscordBridge, InboundMessage
from app.models.character_draft import CharacterDraft
from app.presentation.i18n import bilingual, normalize_locale, tr
from app.presentation.screen import (
    MAX_BUTTON_LABEL,
    ReverieScreen,
    ScreenButton,
    ScreenOption,
    ScreenSelect,
    SelectRow,
)
from app.presentation.screens import (
    DeityChoice,
    SpellChoice,
    deity_selection_screen,
    spell_selection_screen,
)
from app.rules_content import get_registry
from app.services.campaigns.creation_flow import CreationFlowService
from discord_bot.components_v2 import build_layout_view
from discord_bot.render import flatten_screen
from tests.support import screens as S
from tests.support.factories import build_world

# pytest.ini sets asyncio_mode = auto, so async tests need no explicit mark.

_ids = count(1)


def _inbound(content, *, author="disc-p1", name="P1", channel="chan-1", guild="guild-1"):
    return InboundMessage(
        discord_message_id=f"v2-{next(_ids)}", guild_id=guild, channel_id=channel,
        author_discord_id=author, author_display_name=name, content=content,
    )


# ---- view doubles (no network) -----------------------------------------------------

class _Resp:
    def __init__(self):
        self.defer_calls = 0

    async def defer(self):
        self.defer_calls += 1


class _Interaction:
    def __init__(self):
        self.id = next(_ids)
        self.response = _Resp()


# ==================================================================================
# 1. Payload / rendering
# ==================================================================================

def _sample_spell_screen(**over):
    kw = dict(
        pool_kind="prepared", klass="Cleric", required=2,
        chosen=[SpellChoice("bless", "อวยพร", "Bless", "เพิ่ม 1d4", True, True)],
        page_options=[
            SpellChoice("bless", "อวยพร", "Bless", "เพิ่ม 1d4", True, True),
            SpellChoice("cure_wounds", "รักษาแผล", "Cure Wounds", "ฟื้น HP", False, False),
        ],
        select_custom_id="rv-spell-pick-prepared",
        submit_value_template="rvspell:tok:prepared:setpage:0:{values}",
        confirm=ScreenButton("✅", "rvspell:tok:prepared:confirm", "success", disabled=False),
        back=ScreenButton("↩", "rvspell:tok:prepared:back"),
    )
    kw.update(over)
    return spell_selection_screen(**kw)


async def test_layout_view_declares_components_v2_and_carries_no_legacy_content():
    view = build_layout_view(_sample_spell_screen(), lambda i, v: None)
    # discord.py sets the IS_COMPONENTS_V2 message flag from this predicate on send.
    assert view.has_components_v2() is True
    # Exactly one top-level Container; no legacy content/embed is part of the payload.
    payload = view.to_components()
    assert [c["type"] for c in payload] == [17]  # 17 == Container


async def test_container_nesting_is_valid_v2_types():
    view = build_layout_view(_sample_spell_screen(), lambda i, v: None)
    container = view.to_components()[0]
    child_types = {c["type"] for c in container["components"]}
    # Only Text(10), Separator(14), Section(9), ActionRow(1) may sit in a container.
    assert child_types <= {10, 14, 9, 1}
    rows = [c for c in container["components"] if c["type"] == 1]
    # Action rows hold exactly one select or up to five buttons.
    for row in rows:
        kinds = [c["type"] for c in row["components"]]
        assert kinds == [3] or all(k == 2 for k in kinds)  # 3 select, 2 button
        assert len(kinds) <= 5


async def test_required_text_is_present_in_the_rendered_screen():
    text = S.screen_text_from(_sample_spell_screen())
    assert "อวยพร" in text and "Bless" in text            # the selected spell
    assert "2" in text                                     # the required count


async def test_component_limits_are_clamped():
    long = "x" * 300
    screen = spell_selection_screen(
        pool_kind="prepared", klass="Wizard", required=1, chosen=(),
        page_options=[SpellChoice("k", long, long, long, False, False)],
        select_custom_id="rv-spell-pick-prepared",
        submit_value_template="rvspell:t:prepared:setpage:0:{values}",
        confirm=ScreenButton(long, "rvspell:t:prepared:confirm"),
    )
    view = build_layout_view(screen, lambda i, v: None)
    row = next(c for c in view.to_components()[0]["components"] if c["type"] == 1)
    select = row["components"][0]
    assert len(select["options"][0]["label"]) <= 100
    assert len(select["options"][0]["description"]) <= 100
    button = next(c for c in view.walk_children() if isinstance(c, discord.ui.Button))
    assert len(button.label) <= MAX_BUTTON_LABEL


async def test_over_25_options_are_capped_by_the_renderer():
    opts = tuple(ScreenOption(f"o{i}", f"v{i}") for i in range(40))
    screen = ReverieScreen(blocks=(SelectRow(ScreenSelect("cid", "p", opts, 0, 5)),))
    view = build_layout_view(screen, lambda i, v: None)
    select = next(c for c in view.walk_children() if isinstance(c, discord.ui.Select))
    assert len(select.options) == 25


async def test_disabled_button_state_is_rendered():
    screen = _sample_spell_screen(
        confirm=ScreenButton("✅", "rvspell:tok:prepared:confirm", "success", disabled=True))
    view = build_layout_view(screen, lambda i, v: None)
    confirm = next(c for c in view.walk_children()
                   if isinstance(c, discord.ui.Button) and c.label == "✅")
    assert confirm.disabled is True


async def test_multi_select_reentry_uses_submit_template():
    captured = []
    view = build_layout_view(_sample_spell_screen(),
                             lambda i, v: captured.append(v) or asyncio.sleep(0))
    select = next(c for c in view.walk_children() if isinstance(c, discord.ui.Select))
    select._values = ["bless", "cure_wounds"]
    await select.callback(_Interaction())
    assert captured == ["rvspell:tok:prepared:setpage:0:bless,cure_wounds"]


async def test_single_select_reentry_uses_option_value():
    captured = []
    screen = deity_selection_screen(
        stage="deity", klass="Cleric",
        choices=[DeityChoice("เซลูเน่ (Selune)", "เซลูเน่", "Selune", "moon", ("Life",))],
        select_custom_id="rv-deity-deity")
    view = build_layout_view(screen, lambda i, v: captured.append(v) or asyncio.sleep(0))
    select = next(c for c in view.walk_children() if isinstance(c, discord.ui.Select))
    select._values = ["เซลูเน่ (Selune)"]
    await select.callback(_Interaction())
    assert captured == ["เซลูเน่ (Selune)"]


# ==================================================================================
# 2. Localization + design system
# ==================================================================================

def test_bilingual_rule_formats_local_then_english():
    assert bilingual("เซลูเน่", "Selune") == "เซลูเน่ (Selune)"
    assert bilingual("Selune", "Selune") == "Selune"          # no redundant parens


def test_locale_normalization_defaults_to_thai():
    assert normalize_locale(None) == "th"
    assert normalize_locale("en-US") == "en"
    assert normalize_locale("th") == "th"


def test_spell_chrome_translates_without_mixing_languages():
    assert tr("spell_confirm", "en") == "✅ Confirm spells"
    assert tr("spell_confirm", "th") == "✅ ยืนยันคาถา"
    assert "spells" in tr("spell_step", "en", klass="Cleric", required=2)
    assert "คาถา" in tr("spell_step", "th", klass="Cleric", required=2)


def test_deity_screen_uses_one_string_select_not_a_button_wall():
    choices = [DeityChoice(f"D{i} (En{i})", f"D{i}", f"En{i}", "s", ("Life",))
               for i in range(12)]
    screen = deity_selection_screen(stage="deity", klass="Cleric", choices=choices,
                                    select_custom_id="rv-deity-deity")
    assert len(screen.selects()) == 1
    assert len(screen.selects()[0].options) == 12
    # No wall of one-button-per-deity.
    assert screen.buttons() == []


def test_spell_screen_hides_pagination_on_a_single_page():
    one = _sample_spell_screen(page=0, page_count=1,
                               prev_button=None, next_button=None)
    labels = [b.label for b in one.buttons()]
    assert "◀ ก่อนหน้า" not in labels and "ถัดไป ▶" not in labels

    paged = _sample_spell_screen(
        page=0, page_count=3,
        prev_button=ScreenButton("◀ ก่อนหน้า", "rvspell:tok:prepared:previous", disabled=True),
        next_button=ScreenButton("ถัดไป ▶", "rvspell:tok:prepared:next"))
    labels = [b.label for b in paged.buttons()]
    assert "◀ ก่อนหน้า" in labels and "ถัดไป ▶" in labels


def test_spell_screen_is_single_multiselect_with_current_picks_as_defaults():
    screen = _sample_spell_screen()
    assert len(screen.selects()) == 1                        # one menu, not add+remove
    sel = screen.selects()[0]
    assert sel.is_multi
    assert [o.value for o in sel.options if o.default] == ["bless"]


def test_flatten_fallback_preserves_text_and_controls():
    screen = _sample_spell_screen()
    content, buttons, menus = flatten_screen(screen)
    assert "อวยพร" in content                                 # readable as text alone
    assert len(menus) == 1 and menus[0].submit_value_template  # the multi-select
    assert any(b.value.endswith(":confirm") for b in buttons)


# ==================================================================================
# 3. Flow — deity + spell through the real bridge/route path
# ==================================================================================

def _bridges(db, provider):
    flow = CreationFlowService(db, provider)
    return AdminBridge(db, provider, creation_flow=flow), DiscordBridge(db, creation_flow=flow), flow


def _msg(result):
    assert result.handled and len(result.responses) == 1
    return result.responses[0]


async def _seed(db, world, build):
    async with db.unit_of_work() as s:
        s.add(CharacterDraft(campaign_id=world.campaign_id, member_id=world.p1_member_id,
                             data={"name": "Hero", "_build": build}))


async def test_spell_step_renders_v2_and_multiselect_updates_selection(db, provider):
    world = await build_world(db)
    _admin, game, _flow = _bridges(db, provider)
    await _seed(db, world, {
        "step": "prepared", "class": "cleric", "species": "human", "background": "acolyte",
        "scores": {"str": 14, "dex": 10, "con": 13, "int": 8, "wis": 17, "cha": 12},
        "skills": ["religion", "medicine"], "component_token": "tok",
        "cantrips": ["sacred_flame", "guidance", "thaumaturgy"], "prepared": [],
        "spell_pages": {"prepared": 0},
    })

    first = _msg(await game.handle_inbound(_inbound("!rv resume")))
    assert S.has_screen(first) and first.screen is not None
    assert len(S.selects(first)) == 1                        # single multi-select

    # Confirm starts disabled (nothing chosen yet).
    assert S.button_by_action(first, "confirm").disabled is True

    # Choose exactly the required count in ONE multi-select interaction.
    required = get_registry().get_class("cleric").spellcasting.prepared_count
    picks = S.option_values(first)[:required]
    after = _msg(await game.handle_inbound(_inbound(S.multi_submit(first, picks))))
    assert set(S.default_values(after)) == set(picks)        # picks now pre-selected
    assert S.button_by_action(after, "confirm").disabled is False  # exactly required


async def test_deity_step_renders_v2_string_select(db, provider):
    world = await build_world(db)
    _admin, game, _flow = _bridges(db, provider)
    await _seed(db, world, {
        "step": "belief", "belief_stage": "deity", "belief_intent": "believer",
        "class": "cleric", "species": "human", "background": "acolyte",
        "scores": {"str": 14, "dex": 10, "con": 13, "int": 8, "wis": 17, "cha": 12},
        "skills": ["religion", "medicine"], "component_token": "tok",
        "cantrips": ["sacred_flame", "guidance", "thaumaturgy"],
        "prepared": ["cure_wounds", "bless", "guiding_bolt", "shield_of_faith"],
    })
    msg = _msg(await game.handle_inbound(_inbound("!rv resume")))
    assert S.has_screen(msg)
    assert len(S.selects(msg)) == 1                          # a deity String Select
    assert len(S.option_values(msg)) >= 1                    # deities listed, not omitted
