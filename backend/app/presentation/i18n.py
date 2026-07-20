"""Localization for the interactive UI chrome (titles, buttons, counters, errors).

Domain content already carries its own languages (a deity's ``name_th`` /
``canonical_name_en``, a spell's ``name_th_hint`` / ``display_name_en``); this module
covers only the *frame* around it, so callback logic never hardcodes Thai UI strings.

Thai is the established table language and the default. English is provided in full so
the same screens render for an English locale without mixed-language sentences. A
deliberate bilingual rule — Thai name with the English name in parentheses — is
exposed as :func:`bilingual` rather than being open-coded per screen.
"""
from __future__ import annotations

from typing import Literal

Locale = Literal["th", "en"]
DEFAULT_LOCALE: Locale = "th"


def normalize_locale(value: str | None) -> Locale:
    """Resolve a stored/user locale to a supported one, defaulting to Thai."""
    if value:
        head = value.strip().lower().replace("_", "-").split("-", 1)[0]
        if head == "en":
            return "en"
    return "th"


def bilingual(name_local: str, name_en: str | None) -> str:
    """The house rule: local name, English in parentheses when it adds information."""
    name_local = (name_local or "").strip()
    name_en = (name_en or "").strip()
    if name_en and name_en.casefold() != name_local.casefold():
        return f"{name_local} ({name_en})"
    return name_local or name_en


# key -> {locale -> template}. Templates use str.format kwargs.
_STRINGS: dict[str, dict[Locale, str]] = {
    # -- generic chrome --
    "warning_prefix": {"th": "⚠️ {message}", "en": "⚠️ {message}"},
    "resume_footer": {
        "th": "ปุ่มหมดอายุเมื่อไร ใช้ !rv resume ได้เสมอ",
        "en": "Buttons expired? !rv resume reopens this exact step.",
    },
    "stale_control": {
        "th": "ปุ่มนี้มาจากแบบร่างหรือหน้าก่อนและใช้กับขั้นตอนปัจจุบันไม่ได้",
        "en": "That control is from an earlier card and can't be used on this step.",
    },
    # -- deity selection --
    "deity_title": {"th": "⚜ เลือกเทพแห่งศรัทธา", "en": "⚜ Choose a Deity"},
    "deity_step": {"th": "ขั้นตอนศรัทธา · {klass}", "en": "Faith step · {klass}"},
    "deity_step_secondary": {
        "th": "เทพรอง / เทพตามวัฒนธรรม · {klass}",
        "en": "Secondary / cultural faith · {klass}",
    },
    "deity_step_cleric": {"th": "แหล่งพลังของ Cleric · {klass}", "en": "Cleric's power · {klass}"},
    "deity_why": {
        "th": "เทพที่เลือกส่งผลต่อบทบาททางศาสนา ความสัมพันธ์ เนื้อเรื่อง และตัวเลือก Domain",
        "en": "A deity shapes your religious role, relationships, story, and Domain options.",
    },
    "deity_why_cleric": {
        "th": "รายการนี้มีเฉพาะเทพที่มอบพลัง Cleric ได้",
        "en": "Only deities that can grant Cleric power appear here.",
    },
    "deity_instruction": {
        "th": "เลือกจากเมนู หรือพิมพ์ชื่อไทย/อังกฤษ/นามแฝงก็ได้",
        "en": "Pick from the menu, or type a Thai/English name or alias.",
    },
    "deity_placeholder": {"th": "เลือกเทพ…", "en": "Choose a deity…"},
    "deity_none_selected": {
        "th": "ยังไม่ได้เลือกเทพ — เลือกหนึ่งองค์เพื่อไปต่อ",
        "en": "No deity chosen yet — pick one to continue.",
    },
    "deity_hint": {
        "th": "จากที่เจ้าพิมพ์ไว้ ข้าเดาว่าน่าจะเป็นองค์แรกด้านล่าง — เลือกเพื่อยืนยันได้เลย",
        "en": "From what you typed, the first option is my guess — pick it to confirm.",
    },
    "deity_domains": {"th": "Domains", "en": "Domains"},
    "deity_none_available": {
        "th": "แคมเปญนี้ยังไม่ได้เปิดชุดเทพ (pantheon) จึงยังไม่มีรายชื่อเทพให้เลือก\n"
              "ตัวละครยังเป็นผู้ศรัทธาได้ — ยืนยันว่าศรัทธาโดยยังไม่ระบุองค์",
        "en": "This campaign has no active pantheon yet, so there is no deity list.\n"
              "The character can still believe — confirm faith without naming a deity.",
    },
    # -- spell preparation --
    "spell_title_cantrips": {"th": "✨ เลือกคาถาประจำตัว", "en": "✨ Choose Cantrips"},
    "spell_title_book": {"th": "📖 คัดคาถาลงตำรา", "en": "📖 Fill Your Spellbook"},
    "spell_title_prepared": {"th": "✨ เตรียมคาถา", "en": "✨ Prepare Spells"},
    "spell_step": {"th": "{klass} · เลือก {required} คาถา", "en": "{klass} · choose {required} spells"},
    "spell_instruction_cantrips": {
        "th": "เลือก Cantrip ที่ตัวละครใช้ได้โดยไม่เสียช่องเวท",
        "en": "Pick the cantrips your character can cast without spending a slot.",
    },
    "spell_instruction_book": {
        "th": "เลือกคาถาเลเวล 1 ที่จดไว้ในตำราตั้งแต่เริ่มต้น",
        "en": "Pick the level-1 spells written in your book from the start.",
    },
    "spell_instruction_prepared": {
        "th": "เลือกคาถาที่พร้อมใช้เมื่อการผจญภัยเริ่มขึ้น",
        "en": "Pick the spells you have ready when the adventure begins.",
    },
    "spell_count": {"th": "เลือกแล้ว: {count} / {required}", "en": "Selected: {count} / {required}"},
    "spell_selected_header": {"th": "คาถาที่เลือก", "en": "Selected spells"},
    "spell_none_selected": {"th": "ยังไม่ได้เลือกคาถา", "en": "No spells selected yet."},
    "spell_placeholder": {
        "th": "เลือกคาถาที่เตรียมไว้ (หน้า {page}/{pages})…",
        "en": "Choose prepared spells (page {page}/{pages})…",
    },
    "spell_placeholder_single": {
        "th": "เลือกคาถาที่เตรียมไว้…",
        "en": "Choose prepared spells…",
    },
    "spell_concentration": {"th": "เพ่งสมาธิ", "en": "Concentration"},
    "spell_type_hint": {
        "th": "พิมพ์ชื่ออังกฤษหรือชื่อไทยได้เช่นกัน",
        "en": "You can also type the English or Thai name.",
    },
    "spell_confirm": {"th": "✅ ยืนยันคาถา", "en": "✅ Confirm spells"},
    "spell_reset": {"th": "รีเซ็ต", "en": "Reset"},
    "spell_back": {"th": "↩ ย้อนกลับ", "en": "↩ Back"},
    "spell_cancel": {"th": "✖ ยกเลิกการสร้าง", "en": "✖ Cancel creation"},
    "spell_prev": {"th": "◀ ก่อนหน้า", "en": "◀ Prev"},
    "spell_next": {"th": "ถัดไป ▶", "en": "Next ▶"},
    "spell_need_exact": {
        "th": "ต้องเลือกให้ครบ {required} รายการก่อนยืนยัน (ตอนนี้ {count})",
        "en": "Select exactly {required} before confirming (currently {count}).",
    },
    "spell_too_many": {
        "th": "เลือกเกิน {required} รายการ — เอาบางรายการออกก่อน",
        "en": "That is more than {required} — remove some first.",
    },
    "spell_reset_done": {"th": "ล้างตัวเลือกทั้งหมดแล้ว", "en": "Cleared all selections."},
    # -- shared navigation --
    "nav_back": {"th": "↩ ย้อนกลับ", "en": "↩ Back"},
    "nav_continue": {"th": "ดำเนินการต่อ", "en": "Continue"},
}


def tr(key: str, locale: Locale = DEFAULT_LOCALE, /, **kwargs: object) -> str:
    """Look up ``key`` for ``locale`` and format it. Falls back to Thai then the key."""
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    template = entry.get(locale) or entry.get("th") or key
    try:
        return template.format(**kwargs) if kwargs else template
    except (KeyError, IndexError):
        return template
