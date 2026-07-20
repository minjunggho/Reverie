"""Reusable Reverie screen builders — the native design system in one place.

The named design components the whole bot shares (header, status, instruction,
selection summary, option preview, navigation, notices, pagination) live here as
small pure functions that return :mod:`app.presentation.screen` blocks. Two
high-level builders compose them into the migrated screens: deity selection and
spell preparation.

These functions take *view models* (plain data the domain service prepares), never
ORM rows or a registry — so they stay deterministic, cheap to test, and free of
Discord types. A caller builds the view model from authoritative state and gets back
a `ReverieScreen`; rendering and re-validation happen elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from app.presentation.i18n import DEFAULT_LOCALE, Locale, bilingual, tr
from app.presentation.screen import (
    Block,
    ReverieScreen,
    ScreenBuilder,
    ScreenButton,
    ScreenOption,
    ScreenSelect,
    SectionBlock,
    TextBlock,
)

# Accent colours reused from the presentation kind-style vocabulary.
ACCENT_CREATION = 0x6FA8DC
ACCENT_FAITH = 0x8B6FB8
ACCENT_STORY = 0x3E5368


# ---- named design components -------------------------------------------------------

def header(title: str, step: str) -> Block:
    """Screen title with a quiet step/context line beneath it."""
    return TextBlock(f"## {title}\n-# {step}")


def instruction(text: str) -> Block:
    """One short line telling the player what to do."""
    return TextBlock(text)


def status(text: str) -> Block:
    """A prominent current-status line, placed high on the screen."""
    return TextBlock(f"**{text}**")


def warning_notice(message: str, locale: Locale = DEFAULT_LOCALE) -> Block:
    """A transient, non-blocking notice (stale button, boundary hit, bad input)."""
    return TextBlock(tr("warning_prefix", locale, message=message))


def selection_summary(heading: str, lines: Sequence[str], empty: str) -> Block:
    """A compact, scannable list of what is currently chosen."""
    body = "\n".join(lines) if lines else f"-# {empty}"
    return TextBlock(f"**{heading}**\n{body}")


def option_preview(title: str, lines: Sequence[str],
                   accessory: ScreenButton | None = None) -> Block:
    """A detailed preview of the highlighted/selected option."""
    text = title if not lines else title + "\n" + "\n".join(lines)
    return SectionBlock(text=text, accessory=accessory)


def simple_screen(
    *,
    title: str,
    body: str = "",
    buttons: Sequence[ScreenButton] = (),
    step: str | None = None,
    notice: str = "",
    accent: int | None = ACCENT_CREATION,
    locale: Locale = DEFAULT_LOCALE,
) -> ReverieScreen:
    """A header + prose + choice-buttons card, chunked into rows of five.

    The general shape a short-list step uses (a stance choice, a domain pick) so the
    whole flow can be V2 without a bespoke builder per card.
    """
    b = ScreenBuilder(accent=accent)
    if notice:
        b.block(warning_notice(notice, locale))
    if step:
        b.block(header(title, step))
    else:
        b.text(f"## {title}")
    if body.strip():
        b.block(instruction(body))
    kept = [x for x in buttons if x is not None]
    if kept:
        b.separator()
        for i in range(0, len(kept), 5):
            b.buttons(*kept[i:i + 5])
    return b.build()


# ---- player-facing story / shared planning ----------------------------------------

def cinematic_scene_screen(
    *,
    metadata: str,
    narration: str,
    decision_prompt: str,
    planning_window_id: str | None = None,
    planning_status: Sequence[str] = (),
) -> ReverieScreen:
    """One visually connected story container.

    Metadata is deliberately quiet, while prose remains the dominant block.  Shared
    planning status and controls sit beneath the same scene instead of becoming a
    separate campaign-summary card.
    """
    b = ScreenBuilder(accent=ACCENT_STORY)
    b.text(f"-# {metadata}")
    b.separator()
    b.text(narration.strip())
    if decision_prompt:
        b.separator(large=True)
        b.text(f"**{decision_prompt.strip()}**")
    if planning_window_id:
        b.separator()
        status_lines = "\n".join(planning_status) if planning_status else (
            "ส่งการกระทำด้วย `! ...` · แก้ได้จนกว่าทุกคนจะพร้อม"
        )
        b.text(
            "**วางแผนร่วมกัน**\n"
            f"{status_lines}\n"
            "-# ทุกคนส่งหรือแก้การกระทำของตนเองก่อน แล้วค่อยกดพร้อม"
        )
        b.buttons(
            ScreenButton(
                label="พร้อม",
                value=f"~rv-ready:{planning_window_id}",
                style="success",
            ),
            ScreenButton(
                label="ยังไม่พร้อม",
                value=f"~rv-unready:{planning_window_id}",
                style="secondary",
            ),
            ScreenButton(
                label="ผ่านรอบนี้",
                value=f"~rv-pass:{planning_window_id}",
                style="secondary",
            ),
        )
        b.text("-# ผู้ดูแลโต๊ะ")
        b.buttons(
            ScreenButton("บังคับจบรอบ", f"~rv-force:{planning_window_id}", "danger"),
            ScreenButton("เปิดวางแผนใหม่", f"~rv-reopen:{planning_window_id}", "secondary"),
        )
    return b.build()


def decision_window_screen(
    *,
    window_id: str,
    round_id: int,
    cards: Sequence[dict],
    actor_names: dict[str, str],
    viewer_actor_id: str | None = None,
    notice: str = "",
) -> ReverieScreen:
    """A compact authoritative planning panel after a submit/edit/Ready action."""
    b = ScreenBuilder(accent=ACCENT_STORY)
    if notice:
        b.text(f"**{notice}**")
        b.separator()
    b.text(f"### แผนของกลุ่ม · รอบ {round_id}")
    by_actor = {c.get("actor_id"): c for c in cards}
    for actor_id, name in actor_names.items():
        card = by_actor.get(actor_id)
        if card is None:
            b.text(f"○ **{name}** — ยังไม่ได้ส่งการกระทำ")
            continue
        state = "พร้อมแล้ว" if card.get("ready") else "ส่งแล้ว · ยังแก้ได้"
        preview = card.get("preview")
        if card.get("secret") and preview is None:
            preview = "การกระทำลับ"
        detail = f"\n> {preview}" if preview else ""
        b.text(f"{'●' if card.get('ready') else '◐'} **{name}** — {state}{detail}")
    b.separator()
    hint = "พิมพ์ `! ...` อีกครั้งเพื่อแก้การกระทำของคุณ"
    if viewer_actor_id and by_actor.get(viewer_actor_id, {}).get("ready"):
        hint = "คุณพร้อมแล้ว · หากแก้การกระทำ ระบบจะยกเลิก Ready ให้อัตโนมัติ"
    b.text(f"-# {hint}")
    b.buttons(
        ScreenButton("พร้อม", f"~rv-ready:{window_id}", "success"),
        ScreenButton("ยังไม่พร้อม", f"~rv-unready:{window_id}", "secondary"),
        ScreenButton("ผ่านรอบนี้", f"~rv-pass:{window_id}", "secondary"),
    )
    b.text("-# ผู้ดูแลโต๊ะ")
    b.buttons(
        ScreenButton("บังคับจบรอบ", f"~rv-force:{window_id}", "danger"),
        ScreenButton("เปิดวางแผนใหม่", f"~rv-reopen:{window_id}", "secondary"),
    )
    return b.build()


# ---- deity selection ---------------------------------------------------------------

@dataclass(frozen=True)
class DeityChoice:
    """View model for one selectable deity."""

    value: str              # text re-entered on select (owned by the caller)
    name_th: str
    name_en: str | None
    summary: str
    domains: tuple[str, ...] = ()
    selected: bool = False


def _deity_line(d: DeityChoice, locale: Locale) -> str:
    name = bilingual(d.name_th, d.name_en)
    dom = f" · {', '.join(d.domains)}" if d.domains else ""
    mark = "◆ " if d.selected else ""
    return f"{mark}**{name}** — {d.summary}{dom}"


def deity_selection_screen(
    *,
    stage: Literal["deity", "secondary", "cleric_deity"],
    klass: str,
    choices: Sequence[DeityChoice],
    select_custom_id: str,
    placeholder: str | None = None,
    show_hint: bool = False,
    notice: str = "",
    extra_options: Sequence[ScreenOption] = (),
    extra_buttons: Sequence[ScreenButton] = (),
    back: ScreenButton | None = None,
    continue_button: ScreenButton | None = None,
    locale: Locale = DEFAULT_LOCALE,
) -> ReverieScreen:
    """A select-and-preview deity screen: header, why, instruction, current
    selection status, one String Select, a preview of the chosen deity, then
    navigation. Replaces the wall of one-button-per-deity."""
    step_key = {
        "deity": "deity_step",
        "secondary": "deity_step_secondary",
        "cleric_deity": "deity_step_cleric",
    }[stage]
    b = ScreenBuilder(accent=ACCENT_FAITH)
    if notice:
        b.block(warning_notice(notice, locale))
    b.block(header(tr("deity_title", locale), tr(step_key, locale, klass=klass)))
    b.block(instruction(tr("deity_why_cleric" if stage == "cleric_deity" else "deity_why", locale)))
    if show_hint:
        b.text(f"-# {tr('deity_hint', locale)}")
    b.separator()

    selected = next((d for d in choices if d.selected), None)
    status_text = (
        bilingual(selected.name_th, selected.name_en) if selected
        else tr("deity_none_selected", locale)
    )
    b.block(status(status_text))

    options = [
        ScreenOption(
            label=bilingual(d.name_th, d.name_en),
            value=d.value,
            description=d.summary,
            default=d.selected,
        )
        for d in choices
    ]
    options.extend(extra_options)
    b.select(ScreenSelect(
        custom_id=select_custom_id,
        placeholder=placeholder or tr("deity_placeholder", locale),
        options=tuple(options),
        min_values=1,
        max_values=1,
    ))
    b.text(f"-# {tr('deity_instruction', locale)}")

    if selected is not None:
        preview_lines = []
        if selected.domains:
            preview_lines.append(f"**{tr('deity_domains', locale)}:** {', '.join(selected.domains)}")
        b.separator()
        b.block(option_preview(
            f"**{bilingual(selected.name_th, selected.name_en)}**\n{selected.summary}",
            preview_lines,
        ))

    nav = [*extra_buttons]
    if back is not None:
        nav.append(back)
    if continue_button is not None:
        nav.append(continue_button)
    if nav:
        b.separator()
        b.buttons(*nav)
    return b.build()


# ---- spell preparation -------------------------------------------------------------

@dataclass(frozen=True)
class SpellChoice:
    """View model for one spell. ``value`` is the raw spell key for a multi-select."""

    value: str
    name_th: str
    name_en: str
    summary: str
    concentration: bool = False
    selected: bool = False


def _spell_summary_line(s: SpellChoice, locale: Locale) -> str:
    name = bilingual(s.name_th, s.name_en)
    conc = f" · {tr('spell_concentration', locale)}" if s.concentration else ""
    return f"✅ **{name}** — {s.summary}{conc}"


def spell_selection_screen(
    *,
    pool_kind: Literal["cantrips", "book", "prepared"],
    klass: str,
    required: int,
    chosen: Sequence[SpellChoice],
    page_options: Sequence[SpellChoice],
    select_custom_id: str,
    submit_value_template: str,
    page: int = 0,
    page_count: int = 1,
    max_pick: int | None = None,
    notice: str = "",
    confirm: ScreenButton | None = None,
    reset: ScreenButton | None = None,
    back: ScreenButton | None = None,
    cancel: ScreenButton | None = None,
    prev_button: ScreenButton | None = None,
    next_button: ScreenButton | None = None,
    locale: Locale = DEFAULT_LOCALE,
) -> ReverieScreen:
    """A single multi-select spell workflow: header, count status, selected-spell
    summary, one multi-select (existing picks pre-selected), then confirm/reset/back.
    Replaces the separate add + remove menus and the row of disabled spacer buttons."""
    title = tr(f"spell_title_{pool_kind}", locale)
    b = ScreenBuilder(accent=ACCENT_CREATION)
    if notice:
        b.block(warning_notice(notice, locale))
    b.block(header(title, tr("spell_step", locale, klass=klass, required=required)))
    b.block(instruction(tr(f"spell_instruction_{pool_kind}", locale)))
    b.block(status(tr("spell_count", locale, count=len(chosen), required=required)))
    b.separator()
    b.block(selection_summary(
        tr("spell_selected_header", locale),
        [_spell_summary_line(s, locale) for s in chosen],
        tr("spell_none_selected", locale),
    ))
    b.separator()

    cap = max_pick if max_pick and max_pick > 0 else 25
    max_values = max(1, min(len(page_options), cap, 25))
    placeholder = (
        tr("spell_placeholder", locale, page=page + 1, pages=page_count)
        if page_count > 1 else tr("spell_placeholder_single", locale)
    )
    b.select(ScreenSelect(
        custom_id=select_custom_id,
        placeholder=placeholder,
        options=tuple(
            ScreenOption(
                label=bilingual(s.name_th, s.name_en),
                value=s.value,
                description=s.summary,
                default=s.selected,
            )
            for s in page_options
        ),
        min_values=0,
        max_values=max_values,
        submit_value_template=submit_value_template,
    ))
    b.text(f"-# {tr('spell_type_hint', locale)}")

    # Pagination only when there is more than one page.
    if page_count > 1 and (prev_button is not None or next_button is not None):
        page_nav = [x for x in (prev_button, next_button) if x is not None]
        b.buttons(*page_nav)

    b.separator()
    controls = [x for x in (back, reset, confirm) if x is not None]
    if cancel is not None:
        controls.append(cancel)
    if controls:
        b.buttons(*controls)
    return b.build()
