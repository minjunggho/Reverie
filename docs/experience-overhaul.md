# Player & DM Experience Overhaul — Audit, Journey, Gaps, Plan

Evidence base: full repo trace + the live playtest screenshot (2026-07-09, channel
run by minjunggho_): plain-text replies, `!rv` developer commands, a generic
opening scene unrelated to the created character (Veskan, wizard), and a raw
"⚠️ เกิดข้อผิดพลาดภายใน ลองใหม่อีกครั้ง" after the first committed action.

---

## 1. CURRENT EXPERIENCE AUDIT (step-by-step trace)

| Step | What happens today | Why it feels like a database/API workflow |
|---|---|---|
| New user opens channel | Nothing. Bot is silent until a correct command is typed. | No welcome, no orientation. You must already know `!rv`. |
| Join campaign | `!rv campaign new <ชื่อ>` → "✅ สร้างแคมเปญ …" | CRUD confirmation with a checkmark — a database INSERT receipt, not a table being set. |
| Create character | `!rv character Veskan Wizard` → "✅ สร้างตัวละคร Veskan (wizard) — HP 7, AC 12" | Name+class form fill. No concept, origin, desire, fear, flaw, appearance, or party ties. The character is a stat row, and the DM engine has **zero narrative hooks** to use later. |
| Session Zero | Does not exist. | Tone/assistance/boundaries are silent config defaults nobody chose. |
| Start session 1 | `!rv session start` → hardcoded "โรงเตี๊ยมหมาป่าเทา" + generic purpose | Same opening for every party ever. Nothing about *who Veskan is* or why he's here. The screenshot shows this verbatim. |
| Discuss | Classifier answers questions; OK. | Replies are bare text; no visual distinction between DM voice, mechanics, and system notices. |
| Commit `!` action | Full pipeline runs correctly. | Output (when it works) is one dense paragraph; mechanics invisible or absent; no decision point offered. |
| Adjudication/dice | Correct and authoritative. | The *player* never sees the roll in a readable form — the best part of the engine is hidden. |
| Narration | One `Narration.text` blob, single message. | No progressive disclosure; long paragraphs; no line-break discipline enforced. |
| NPC/social | Engine exists (`NPCSocialService`) but **is not reachable from Discord at all**. | Talking to the guard does nothing special. |
| Inspect character/inventory | Impossible. No sheet, no inventory (InventoryEntry was never built), no journal. | Players can't even see their own HP without `!rv status` (a table dump). |
| Errors | Raw "⚠️ เกิดข้อผิดพลาดภายใน ลองใหม่อีกครั้ง" regardless of what failed. | Screenshot evidence. Immersion killed; player can't know if their action counted. |
| End session | `!rv session end` → summary + recap concatenated as text. | No closing beat, no chronicle structure, no feedback moment. |
| Return next session | `session start` reuses the same hardcoded tavern; recap exists but openings ignore continuity of place/pressure. | Session 2 looks identical to session 1. |

**Root causes** (not cosmetics):
1. No presentation layer — every reply is an undifferentiated string.
2. Characters carry no narrative data, so nothing downstream *can* be personal.
3. Openings are hardcoded, not generated from party + hooks + pressure.
4. Error handling doesn't consult the pipeline stage it already records.
5. Social/NPC, inventory, and sheet subsystems are engine-only, unreachable.

## 2. TARGET PLAYER JOURNEY

1. **Welcome** — first `!rv` (or the bot joining) yields a warm REVERIE_WELCOME embed that reads the table state and says *what to do next* in friendly Thai.
2. **Join** — joining feels like sitting down at a table, not registering.
3. **Character creation** — `!rv character` starts a short guided conversation (concept → origin/desire/fear → flaw/party tie → confirm mechanics), in natural Thai, ~4 exchanges, ending in a CHARACTER_REVEAL embed with real hooks. Quick path (`!rv character <name> <class>`) stays for impatient friends.
4. **Session Zero** — owner runs `!rv setup`: 4 quick, friendly questions (tone, balance, assistance, boundaries) answered by button or text; stored on the campaign profile.
5. **Session 1** — AI-generated opening that names the characters, ties at least one established hook into the situation, sets a pressure, and ends on one open decision point. Rendered as SESSION_TITLE + SCENE_FRAME.
6. **Play** — talk freely; `!` Thai actions resolve into ONE structured message: short narration lines + a visible dice line (🎲 Stealth 16+5=21 vs DC15 — สำเร็จ) + a decision prompt when one exists.
7. **NPC** — addressing a visible NPC gets an in-register reply from that NPC's knowledge only (NPC_DIALOGUE).
8. **Sheet/inventory/journal/party** — `!rv sheet` / `!rv inventory` / `!rv journal` / `!rv party` render structured views; secrets arrive by DM (PRIVATE_SECRET).
9. **Close** — a deliberate closing beat, then a SESSION_END chronicle (decisions, discoveries, items, objectives, open questions), then one light emoji-button feedback ask.
10. **Return** — next `session start` gives a concise PLAYER_SAFE_RECAP, current place/time/pressure, and a fresh decision point; continuity restored from canonical state.

## 3. GAP ANALYSIS

| Gap | Severity | Fix |
|---|---|---|
| No presentation vocabulary/renderer | High | `MessageKind` enum + typed `OutboundMessage(kind, data, choices)`; Discord renderer maps kinds → embeds/colors; engine stays Discord-free. |
| Characters have no hooks | High | `Character.hooks` JSON + `appearance`; guided creation flow (`CharacterDraft` state machine + `CharacterCreationGuide` AI job). |
| Hardcoded openings | High | `SessionOpeningService` split: Session 1 uses `SessionOpeningGenerator` (AI) fed by a bounded scene-context builder (hooks, profile, pressure); later sessions use recap+restore. |
| Raw error message | High | Bridge-level recovery keyed to `ProcessedMessage.stage` → four distinct player-safe TECHNICAL_ERROR messages; never re-execute after COMMITTED. |
| Dice invisible / narration dense | High | Structured CHECK_RESOLUTION data on the result message; narration prompt rewritten for progressive disclosure + `decision_prompt`; banned-phrase list. |
| No inventory/sheet/journal/party | Med | `ItemDefinition`/`InventoryEntry` models + `InventoryService` + class starting gear; view builders (engine) + `!rv sheet/inventory/journal/party`. Journal derives from player-visible events (no new table). |
| NPC social unreachable | Med | Route CHARACTER_DIALOGUE addressed to a visible NPC → `NPCSocialService` → NPC_DIALOGUE message. |
| No Session Zero | Med | `!rv setup` conversational flow (state in campaign.config), 4 questions max, choices rendered as buttons. |
| No private-secret delivery | Med | `reveal_secret` consequence delta: may only reveal a **pre-authored** Secret row (engine-validated), delivered via DM as PRIVATE_SECRET; never inventable by the LLM. |
| No closing beat/feedback | Low | Closing beat via narrator; chronicle structure; one-tap emoji feedback stored on `Session.feedback`. |

## 4. IMPLEMENTATION PLAN (incremental slices, tests per slice)

- **X1 Presentation core** — MessageKind; OutboundMessage gains `kind/data/choices`; Discord renderer (embeds, colors, button-choices that round-trip as synthetic inbound text); welcome command.
- **X2 Error recovery UX** — bridge wraps the committed pipeline; stage-aware Thai recovery messages; committed-but-unnarrated returns the factual result.
- **X3 Readable resolution** — pipeline emits one structured result message (narration lines + dice data + decision prompt); narration policy prompts rewritten.
- **X4 Inventory & views** — items models/service, starting gear, sheet/inventory/journal/party views, ITEM_GAINED events.
- **X5 Character creation** — hooks columns, CharacterDraft, guided flow + reveal; quick path preserved.
- **X6 Session Zero** — profile flow + storage.
- **X7 Openings/closings** — Session-1 generator w/ hooks + scene context builder; later-session recap opening; closing beat + chronicle + feedback.
- **X8 NPC dialogue routing + private secrets.**
- **X9 Acceptance journey test** (two players, full §-flow) + Thai eval fixtures (`backend/evals/`).

## 5. FILES / MODULES TO CHANGE
New: `app/presentation/`, `app/models/character_draft.py`, `app/models/items.py`,
`app/services/campaigns/creation_flow.py`, `app/services/campaigns/inventory_service.py`,
`app/services/campaigns/session_zero.py`, `app/services/views.py`,
`app/ai/jobs/creation_guide.py`, `app/ai/jobs/opening.py`, `discord_bot/render.py`,
`backend/evals/`, tests `test_experience_*.py`, `test_acceptance_journey.py`.
Changed: `discord_bridge/dto.py`, `bridge.py`, `admin_bridge.py`, `orchestration/pipeline.py`,
`orchestration/router.py`, `models/character.py`, `models/session.py`, `models/__init__.py`,
`schemas/llm_io.py`, `ai/llm/base.py`, `ai/prompts/*`, `memory/context_builders.py`,
`services/sessions/opening_service.py`, `closing_service.py`, `tabletop/adjudication/deltas.py`,
`discord_bot/client.py`, `tests/support/fake_script.py`.

## 6. MIGRATION / DATABASE CHANGES
- `characters` + `hooks` JSON, `appearance` TEXT (defaults; additive).
- new `character_drafts` (creation conversation state).
- new `item_definitions`, `inventory_entries`.
- `sessions` + `feedback` JSON.
- `campaigns.config` gains `profile` / `setup_state` / `spotlight` keys (JSON — no DDL).
Tests use `create_all`; production regenerates the Alembic autorevision. All changes additive.

## 7. NEW TEST PLAN
Automated (state/auth/resolution/recovery/continuity):
- presentation: result/welcome/reveal messages carry correct kind + structured data.
- creation: guided flow → Character with valid mechanics + non-empty hooks; draft
  messages never hit the classifier; quick path unchanged.
- inventory: starting gear on creation; ITEM_GAINED event; view data correct.
- error staging: pre-commit failure vs post-commit narration failure produce the two
  distinct messages; post-commit never re-rolls (extends existing invariant test).
- openings: session 1 context contains hooks + profile and the generated opening is
  used; session ≥2 opening contains recap and current place/time.
- NPC dialogue: routed reply uses only NPC-known facts (existing epistemic tests extended).
- secrets: `reveal_secret` delta only reveals an existing Secret, delivered privately,
  absent from public output and recaps.
- closing: chronicle fields populated from events; feedback stored.
- **Acceptance journey**: the full two-player experience end-to-end on FakeLLM.
Manual (presentation/Thai): `backend/evals/` fixtures + `run_eval.py` (real provider,
skipped without a key) — inspect narration for progressive disclosure, banned phrases,
register, and length discipline.
