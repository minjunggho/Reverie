"""Deploy-verification version stamps (E7).

One place that answers "which Reverie is actually running?" — surfaced by the
owner-only `!rv diagnostics` command so the live bot is verifiably the same code
and content that passed tests. Bump a stamp when its subsystem's behavior or
storage format changes.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

# The world model this code writes (campaign anchors, canonical positions, graph).
WORLD_MODEL_VERSION = 2
# Campaign importer behavior (sections understood + commit semantics).
IMPORTER_VERSION = 2
# Prompt-pack revision (system prompts across AI jobs).
PROMPT_VERSION = 3
# Memory/continuity subsystem revision.
MEMORY_SYSTEM_VERSION = 1

# Process start (UTC) — the "build/boot" timestamp diagnostics reports.
PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")


@lru_cache(maxsize=1)
def git_sha() -> str:
    """Deployed revision: REVERIE_GIT_SHA when the deploy sets it, else the local
    git checkout, else 'unknown'. Never raises."""
    env = os.environ.get("REVERIE_GIT_SHA", "").strip()
    if env:
        return env
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parent,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 - diagnostics must never crash the bot
        pass
    return "unknown"


@lru_cache(maxsize=1)
def rules_content_hash() -> str:
    """Stable hash over every rules-content JSON file (order-independent name+bytes).
    Two deployments with the same hash serve identical rules content."""
    content_dir = Path(__file__).resolve().parent.parent / "rules_content" / "srd_5_2_1"
    digest = hashlib.sha256()
    for path in sorted(content_dir.glob("*.json")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]
