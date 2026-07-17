"""The Alembic chain is the deployment path — prove it works.

History: Alembic was wired in 292d76c but the initial schema revision was never
generated, and 20260710_canon declared a parent (20260710_aliases) that was never
committed. The chain was reconstructed on 2026-07-12 (root 20260708_core + the
missing aliases revision). These tests pin the repair:

- an EMPTY database upgrades to head;
- the migrated schema is structurally IDENTICAL to ``Base.metadata.create_all``
  (columns, indexes, foreign keys) — the app runs on migrations alone;
- an EXISTING pre-revamp database (data at 20260710_canon) upgrades with every
  row intact and the anchors backfill applied;
- recent revisions downgrade and re-upgrade cleanly.

Alembic runs in a subprocess exactly as an operator would run it, so settings
caching inside the test process can never mask a broken chain.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
HEAD_REVISION = "20260725_clues"
PRE_REVAMP_REVISION = "20260710_canon"


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["REVERIE_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-q", *args],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    )
    return result


def _schema(path: Path) -> dict:
    conn = sqlite3.connect(path)
    tables: dict = {}
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
    ):
        cols = {
            c[1]: (c[2].upper().split("(")[0], bool(c[3]), bool(c[5]))
            for c in conn.execute(f"PRAGMA table_info({name})")
        }
        idx = {}
        for _, iname, unique, origin, _p in conn.execute(f"PRAGMA index_list({name})"):
            if origin == "pk":
                continue
            columns = tuple(r[2] for r in conn.execute(f"PRAGMA index_info({iname})"))
            key = iname if not iname.startswith("sqlite_autoindex") else f"auto:{columns}"
            idx[key] = (columns, bool(unique))
        fks = {
            (row[3], row[2], row[4])
            for row in conn.execute(f"PRAGMA foreign_key_list({name})")
        }
        tables[name] = (cols, idx, fks)
    conn.close()
    return tables


def _head_of(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT version_num FROM alembic_version").scalar() \
            if hasattr(conn.execute(""), "scalar") else \
            conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        conn.close()


def test_fresh_database_upgrades_to_head(tmp_path):
    db = tmp_path / "fresh.sqlite3"
    _alembic(db, "upgrade", "head")
    conn = sqlite3.connect(db)
    head = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert head == HEAD_REVISION
    # Spot-check the tables each era of the chain owns.
    assert {"campaigns", "characters", "sessions", "scenes", "events",     # core
            "canon_imports",                                               # canon
            "wallets", "currency_transactions",                            # economy
            "npc_memories",                                                # npc memory
            "character_drafts"} <= tables


def test_migrated_schema_matches_create_all(tmp_path):
    """Structural parity: the chain and the models describe the SAME database."""
    import asyncio

    migrated = tmp_path / "migrated.sqlite3"
    _alembic(migrated, "upgrade", "head")

    created = tmp_path / "created.sqlite3"

    async def _create():
        from app.db.session import Database

        db = Database(f"sqlite+aiosqlite:///{created.as_posix()}")
        await db.create_all()
        await db.dispose()

    asyncio.run(_create())

    mig, ca = _schema(migrated), _schema(created)
    problems: list[str] = []
    for t in sorted(set(mig) | set(ca)):
        if t not in mig:
            problems.append(f"table {t} missing from migrated schema")
            continue
        if t not in ca:
            problems.append(f"table {t} exists only in migrated schema")
            continue
        mcols, midx, mfk = mig[t]
        ccols, cidx, cfk = ca[t]
        if mcols != ccols:
            problems.append(f"{t}: columns differ {set(mcols) ^ set(ccols) or 'types'}")
        if midx != cidx:
            problems.append(f"{t}: indexes differ {midx} != {cidx}")
        if mfk != cfk:
            problems.append(f"{t}: foreign keys differ")
    assert not problems, "\n".join(problems)


def _seed_pre_revamp(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    now = "2026-07-12 10:00:00"
    conn.execute(
        "INSERT INTO users (id, discord_user_id, display_name, created_at, updated_at) "
        "VALUES ('u1','disc-1','Nick',?,?)", (now, now))
    conn.execute(
        "INSERT INTO campaigns (id, name, discord_guild_id, game_channel_id, owner_user_id,"
        " config, current_game_time, brief, central_question, session_prep, status,"
        " event_seq, created_at, updated_at)"
        " VALUES ('c1','Last Funeral','g1','ch1','u1','{}',480,'brief','q',?,'ACTIVE',0,?,?)",
        (json.dumps({"opening_location_id": "loc-tavern"}), now, now))
    conn.execute(
        "INSERT INTO campaign_members (id, campaign_id, user_id, role, active_character_id,"
        " created_at, updated_at) VALUES ('m1','c1','u1','OWNER','ch1',?,?)", (now, now))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(characters)")]
    row = {c: None for c in cols}
    row.update(dict(
        id="ch1", campaign_id="c1", owner_member_id="m1", name="Veskan", species="human",
        char_class="wizard", background="sage", ruleset_id="srd521",
        str_score=8, dex_score=12, con_score=13, int_score=15, wis_score=14, cha_score=10,
        proficiencies="[]", expertise="[]", save_proficiencies="[]", tool_proficiencies="[]",
        languages="[]", proficiency_bonus=2, hp=8, max_hp=8, temp_hp=0, ac=12, speed=30,
        hit_die=6, hit_dice_remaining=1, death_saves="{}", stable=0, dead=0, level=1, xp=0,
        exhaustion=0, conditions="[]", resources="{}", location_id="loc-tavern", hooks="{}",
        appearance="", aliases='["เวสกัน"]', created_at=now, updated_at=now))
    conn.execute(
        f"INSERT INTO characters ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
        list(row.values()))
    conn.execute(
        "INSERT INTO npcs (id, campaign_id, name, personality, voice_register, goals,"
        " current_location_id, attitudes, emotional_state, communication_mode,"
        " created_at, updated_at)"
        " VALUES ('n1','c1','Guard','bored','curt','[]','loc-tavern','{}','neutral','SPOKEN',?,?)",
        (now, now))
    conn.execute(
        "INSERT INTO npc_relationships (id, npc_id, entity_ref, attitude, trust,"
        " created_at, updated_at) VALUES ('r1','n1','character:ch1','friendly',5,?,?)",
        (now, now))
    conn.execute(
        "INSERT INTO sessions (id, campaign_id, number, status, active_play_state,"
        " attendance, feedback, version, created_at, updated_at)"
        " VALUES ('s1','c1',1,'COMPLETE','TABLE_OPEN','[]','{}',1,?,?)", (now, now))
    conn.commit()
    conn.close()


def test_existing_database_upgrades_with_data_intact(tmp_path):
    db = tmp_path / "existing.sqlite3"
    _alembic(db, "upgrade", PRE_REVAMP_REVISION)
    _seed_pre_revamp(db)
    _alembic(db, "upgrade", "head")

    conn = sqlite3.connect(db)
    camp = conn.execute(
        "SELECT name, starting_location_id, world_model_version, active_pantheon_keys "
        "FROM campaigns").fetchone()
    char = conn.execute(
        "SELECT name, aliases, following_character_id, location_id, belief_profile, "
        "cleric_deity_key, cleric_domain FROM characters").fetchone()
    npc_belief = conn.execute("SELECT belief_profile FROM npcs").fetchone()[0]
    rel = conn.execute(
        "SELECT attitude, trust, familiarity, current_stance FROM npc_relationships").fetchone()
    sess = conn.execute("SELECT number, status FROM sessions").fetchone()
    conn.close()

    assert camp[:3] == ("Last Funeral", "loc-tavern", 2)  # backfilled from session_prep
    assert json.loads(camp[3]) == []                     # faith packs are opt-in
    assert char[0] == "Veskan" and char[3] == "loc-tavern"
    assert json.loads(char[1]) == ["เวสกัน"]           # aliases survive
    assert char[2] is None                              # follow defaults to no consent
    assert char[4:] == (None, None, None)               # existing character has no belief
    assert npc_belief is None                           # existing NPC has no belief
    assert rel == ("friendly", 5, 0, "neutral")         # old fields kept, dims defaulted
    assert sess == (1, "COMPLETE")


def test_recent_revisions_downgrade_and_reupgrade(tmp_path):
    db = tmp_path / "roundtrip.sqlite3"
    _alembic(db, "upgrade", PRE_REVAMP_REVISION)
    _seed_pre_revamp(db)
    _alembic(db, "upgrade", "head")
    _alembic(db, "downgrade", "-1")                     # drop pantheon activation
    _alembic(db, "upgrade", "head")
    _alembic(db, "downgrade", "20260711_anchors")       # drop npcmem+follow+economy
    _alembic(db, "upgrade", "head")

    conn = sqlite3.connect(db)
    head = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    camp = conn.execute("SELECT name, starting_location_id FROM campaigns").fetchone()
    char = conn.execute("SELECT name FROM characters").fetchone()
    conn.close()
    assert head == HEAD_REVISION
    assert camp == ("Last Funeral", "loc-tavern")
    assert char == ("Veskan",)
