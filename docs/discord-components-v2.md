# Discord Components V2 UI System

Reverie's interactive screens are described **once**, as framework-neutral data, and
rendered natively with Discord **Components V2**. There is no Discord Activity here —
everything is native messages, buttons, select menus, and modals.

## Why this shape

The engine never imports discord.py. It emits a presentation contract
(`OutboundMessage`), and the Discord adapter renders it. Interactive controls
**re-enter normal message routing** — clicking a button or picking an option feeds a
value back through the same inbound path as if it were typed, which is then
re-validated against authoritative state. That is why every screen is already
reproducible from stored state and `!rv resume` "just works".

Components V2 slots into this without changing the philosophy:

```
domain state ─▶ ReverieScreen (declarative)          app/presentation/screen.py
                    │                                  app/presentation/screens.py  (builders)
                    ▼
         ┌── V2 renderer ──────────▶ discord.ui.LayoutView   discord_bot/components_v2.py
         └── legacy flatten ───────▶ text + ChoiceView        discord_bot/render.py
```

- **`ReverieScreen`** is pure data (header/status/summary/select/preview/buttons). No
  discord.py types, no DB. Fully asserted in tests.
- **The V2 renderer** maps it to a `LayoutView` (Container → Section / TextDisplay /
  Separator / ActionRow). discord.py sets the `IS_COMPONENTS_V2` message flag
  automatically; such a message carries **no** legacy content or embed.
- **The flatten fallback** degrades the *same* screen to plain text + a `ChoiceView`
  when `REVERIE_DISCORD_COMPONENTS_V2_ENABLED=false`. One screen definition, two
  renderers — neither uses embeds.

discord.py **2.6+** is required (we run 2.7.1). `requirements.txt` pins `>=2.6`.

## Building a new screen

Compose a `ReverieScreen` from the reusable design components in
`app/presentation/screens.py` — never construct discord.py objects in a service.

```python
from app.presentation.screens import (
    header, instruction, status, selection_summary, option_preview, simple_screen,
)
from app.presentation.screen import ScreenBuilder, ScreenButton, ScreenSelect, ScreenOption
from app.presentation.i18n import tr

def my_screen(*, locale, ...):
    return (
        ScreenBuilder(accent=0x6FA8DC)
        .block(header(tr("my_title", locale), tr("my_step", locale, klass=klass)))
        .block(instruction(tr("my_hint", locale)))
        .block(status(tr("my_count", locale, count=n, required=r)))
        .separator()
        .select(ScreenSelect(
            custom_id="rv-something",
            placeholder=tr("my_placeholder", locale),
            options=tuple(ScreenOption(label=..., value=..., description=..., default=...)
                          for ...),
            min_values=0, max_values=r,
            submit_value_template="rvthing:<token>:set:{values}",   # multi-select only
        ))
        .buttons(ScreenButton(tr("nav_continue", locale), "<value>", style="success",
                              disabled=not valid))
        .build()
    )
```

Then return it from a flow step:

```python
return _screen_card(channel_id, my_screen(locale=self._locale(data), ...))
```

Rules of thumb:

- **Values carry the re-entry text.** A button's `value` and a single-select option's
  `value` are what the engine receives on click. Keep them stable and namespaced
  (`rvspell:<token>:…`, `rv-deity-<stage>`), never large serialized state.
- **Multi-select** sets `submit_value_template`; the adapter substitutes `{values}`
  with the chosen option values (comma-joined) so a whole selection submits as one
  re-entry. The handler re-validates the set server-side.
- **Localize chrome** via `app/presentation/i18n.py` (`tr`, `bilingual`). Domain names
  (deities, spells) already carry `name_th`/`name_en`; use `bilingual()` for the
  "ไทย (English)" rule. Never hardcode Thai UI strings in callback logic.
- **Limits are clamped in the renderer** (25 options, 100/100/100 label/value/desc,
  150 placeholder, 80 button label, 5 buttons/row, 4000 text budget). Builders stay
  declarative.

## Interaction safety (unchanged, and free)

Because controls re-enter routing, all the existing guarantees still hold:
identity is taken from `interaction.user` (never the message), one closure
(`ReverieClient._make_on_choice`) authorizes and routes every click, draft writes are
compare-and-update on `draft.version`, and stale controls are rejected by
component tokens (`rvspell:<token>:…`) and per-stage control allow-lists. Double
clicks, delayed/expired interactions, restarts, and two drafts from one user are all
covered by existing tests (`test_production_discord_callbacks.py`) which now also
exercise the V2 `LayoutView` callbacks.

## Before / after

**Deity selection** — was a wall of up to 23 quick-reply **buttons** plus a markdown
list, silently truncating a 38-deity pantheon, in a legacy embed. Now a single
**String Select** with per-option descriptions, a highlighted-selection preview
(name · summary · Domains), campaign-safe pagination (24/page, prev/next only when
needed — **no deity is omitted**), rendered as Components V2. Typed names and the
"believe without naming a deity" fallback still work.

**Prepared spells** — was **two** select menus (add + remove), a full row that
included **disabled** page/count spacer buttons, and pagination even on one page, in a
legacy embed. Now **one multi-select** whose current picks are the default-selected
values (change the whole selection in one interaction), a compact selected-spell
summary (effect · concentration), a count status, a confirm that is **disabled until
exactly the required count is chosen**, a reset, and pagination that **appears only
when there is more than one page**. Selections are preserved across pages and
re-validated server-side (client limits are never trusted).

## Feature flag

`REVERIE_DISCORD_COMPONENTS_V2_ENABLED` (default **true**). Off = the same screens
flatten to text + a `ChoiceView`. It is a rollout valve, not a second permanent UI.

## Remaining legacy screens — migration priority

The migration is deliberately staged. Migrated: the belief/deity family (stance,
primary/secondary/cleric deity, details, cleric domain, no-pantheon) and spell
selection (cantrips / spellbook / prepared). Still legacy, in recommended order:

| Priority | Screen(s) | Where | Why / notes |
|---|---|---|---|
| 1 | Character-creation Stage-B steps (class, subclass, species, background, abilities, ASI, skills, expertise, review) | `build_flow.py` `_card()` steps | Same wizard as the migrated steps; route `_card` through `simple_screen` for one consistent V2 flow. Review adds a confirm/summary screen. |
| 2 | Stage-A reflection card ([ถูกต้อง]/[แก้ไข]) | `creation_flow.py` `_reflection_card` | Two-button confirm; trivial `simple_screen` swap. |
| 3 | Character sheet / spells / inventory / journal / party views | `app/services/views.py` | Read-mostly; V2 Sections + Separators improve scanning. Keep as embeds until a redesign pass — lower urgency. |
| 4 | Admin/table notices, help, diagnostics | `admin_bridge.py` | Informational; migrate opportunistically. |
| — | Narration (scenes, NPC dialogue, checks, cinematic prologue) | narration pipeline | **Leave as-is** — prose-first messages gain nothing from components. |

Design the next screen with the same primitives; do not hand-build payloads.
