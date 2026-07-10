# Thai narration evals (manual inspection)

Automated tests cover state/auth/dice/recovery. Presentation quality — register,
progressive disclosure, banned phrases — needs eyes. These fixtures make that a
5-minute routine instead of ad-hoc poking.

## Run
```bash
cd backend
python -m evals.run_eval            # uses the REAL provider from your .env
python -m evals.run_eval --task narration   # one task only
```
Requires a real `REVERIE_LLM_PROVIDER` + key. Prints each fixture's context and the
generated output side by side.

## What to check on every output (docs/thai-dm-style.md)
1. **Shape**: 2–5 short lines, line-broken between beats — never one dense block.
2. **Order**: immediate change → result → decision-relevant detail → hook/pressure.
3. **Register**: natural Thai; NPC voices distinct; no translated-fantasy prose.
4. **Banned phrases** (unless grounded in established facts): สัญลักษณ์ลึกลับ /
   บรรยากาศน่าขนลุก / ความรู้สึกที่อธิบายไม่ได้ / พลังงานโบราณ / การมีอยู่อันมืดมิด.
5. **Boundaries**: never states a PC's emotion, never invents mechanics/items/time,
   no roll numbers inside the prose, no secrets surfacing "for drama".
6. **Decision point**: when present, exactly one open question, not a menu.
