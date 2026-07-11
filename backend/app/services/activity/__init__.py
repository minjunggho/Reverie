"""Activity projections (E6) — read-only JSON views over canonical state.

Player-safe projections live in `grimoire.py`; DM-authorized projections live in
`studio.py`. They are SEPARATE modules on purpose: a player route physically cannot
call a studio builder by accident, and no builder ever returns an ORM row.
All derived numbers come from the existing derivation engine — the Activity never
recomputes rules in TypeScript.
"""
