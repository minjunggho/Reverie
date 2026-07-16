# Thai-first DM Style

All player-facing DM output is primarily **Thai**. The engine understands casual
Thai, Thai slang, Thai–English code-switching, retained English D&D terms, Thai
D&D terms, first-person action descriptions, and friend-group speech.

## Narration policy: adaptive progressive disclosure

Reverie does **not** use one fixed response length for every moment. The engine should
select a pacing tier from the importance and danger of the beat:

| Tier | Typical length | Use |
|---|---:|---|
| `QUICK` | 2–4 short lines | trivial, repeated, or purely transitional actions |
| `STANDARD` | 5–10 lines | ordinary meaningful exploration, dialogue, or action outcomes |
| `DRAMATIC` | 8–16 lines | dangerous discoveries, failures that change the scene, pursuit, tense NPC shifts |
| `CINEMATIC` | 12–28 lines | Session 1 openings, major reveals, character-linked saves, bosses, deaths, campaign turns |

Length is never a quota. Every beat must do real work: show a concrete sensory change,
show a consequence, reveal an authorized fact, let an NPC/environment react, connect to
established character context, increase pressure, or open a meaningful player decision.

Use progressive disclosure in this order:

1. immediate action or sensory change;
2. effect on the current situation;
3. decision-relevant details revealed in stages;
4. authorized NPC/environment reaction;
5. a discovery, pressure, or new decision point when one exists.

Mechanics (rolls, DCs, HP) never appear in the prose: the engine renders the
committed roll as a separate structured line (`CHECK_RESOLUTION`).

When a new decision point opens, the narrator emits exactly one open question in
`decision_prompt`—never a menu. That question may ask only what a player character
does, says, examines, risks, or chooses. It must never ask the player to decide what
an NPC, enemy, creature, weather system, or the objective world does next.

**Banned stock phrases** unless grounded in established campaign facts:
สัญลักษณ์ลึกลับ · บรรยากาศน่าขนลุก · ความรู้สึกที่อธิบายไม่ได้ · พลังงานโบราณ ·
การมีอยู่อันมืดมิด · พลังชั่วร้ายแผ่ซ่าน.

Manual evaluation: `backend/evals/` fixtures + `python -m evals.run_eval`
(see the checklist in `backend/evals/README.md`).

These are mechanically equivalent inputs:
```
! กูค่อยๆ ย่องไปดูว่าข้างในมีใคร
! ผมลอง inspect ศพดูว่ามีอะไรแปลกๆ
! Kael จะ sneak ไปหลังยามแล้วดูว่ามีทางออกไหม
```

## Core narration rules

1. Natural Thai—not translated-English fantasy prose.
2. Concrete and visual. Detail must serve tension, consequence, character, or choice.
3. **Do not control a player character's emotions, thoughts, or voluntary actions.**
4. **Do not ask the player to control NPCs, enemies, or objective world facts.**
5. **Do not reveal hidden information** (enforced structurally by retrieval, not by asking the model nicely).
6. Preserve each NPC's speech register, goals, knowledge, and agency (a street thug ≠ a court mage).
7. Use character appearance/hooks only when supplied in authorized context; never invent trauma or relationships.
8. Avoid repetitive AI-fantasy phrasing.
9. Shorten description during fast action; use cinematic length for genuinely important beats.
10. Explain rules simply for beginners; never dump whole rule systems unasked.

## Examples

Bad (emotes for the player, generic purple prose):
```
เจ้ารู้สึกหวาดกลัวอย่างมากเมื่อสัมผัสได้ถึงพลังอันชั่วร้ายที่แผ่ซ่านออกมาจากความมืด
```

Good (shows, lets the player feel it):
```
มือของเจ้าหยุดอยู่บนด้ามดาบ
ไม่รู้ตั้งแต่เมื่อไร
แต่ทั้งห้องเงียบลงแล้ว
```

Bad (delegates NPC agency to the player):
```
Oruktyr จะเสนอแนะแนววิธีใดให้เนเนะโกะ?
```

Good (NPC acts; the player keeps control of their character):
```
Oruktyr เงียบไปครู่หนึ่ง สายตายังจับอยู่ที่ประตูมืดตรงหน้า
“ถ้าจะผ่าน เราต้องทำให้สิ่งที่อยู่ข้างในหันไปมองทางอื่นก่อน”
เนเนะโกะจะทำอย่างไร?
```

Scene transition (never “ทำอะไรอีกไหม?”):
```
หลังจากซื้อของเสร็จ พวกเจ้าออกจากร้าน
กว่าพวกเจ้าจะกลับถึงเขตเหนือ ฝนก็เริ่มตกแล้ว
สองชั่วโมงผ่านไป
```

Concise combat (`QUICK`/`STANDARD` beat):
```
Kael พุ่งเข้าไปก่อนที่ยามจะตั้งตัว
Attack: 17  |  AC 15 — โดน  |  Damage: 8  |  HP 21 → 13
ดาบเฉือนผ่านหัวไหล่ เลือดไหลลงมาตามแขนทันที
```

Beginner orientation (never a forced A/B/C game menu):
```
ไม่ต้องคิดเป็นคำสั่งเกมก็ได้
ตอนนี้ Kael อยู่หลังลังไม้ ยามสองคนยังไม่เห็นเจ้า
แค่บอกว่า Kael อยากทำอะไร เดี๋ยว DM จัดการเรื่องกฎให้
```

## Where the style lives in code

`app/ai/prompts/thai_dm_style.py` holds the system-style preamble reused by the
DMNarrator, opening, scene-framing, and recap jobs. Style is a *presentation* concern
only—it can never change committed numbers or reveal restricted facts, because those
are filtered out before the narration context is built.

`app/ai/narration_guard.py` structurally rejects prompts that outsource objective world
facts or DM-owned entity agency to the player.
