"""Manual Thai-narration eval runner. Uses the REAL provider from your .env.

    python -m evals.run_eval [--task narration|opening|creation]

Prints each fixture context and the generated output for human inspection against
the checklist in evals/README.md. Never run in CI (needs a real key).
"""
from __future__ import annotations

import argparse
import asyncio

from app.ai.llm import get_provider
from app.ai.prompts.system_prompts import CREATION_GUIDE_SYSTEM, NARRATOR_SYSTEM_EXTRA, OPENING_SYSTEM
from app.ai.prompts.thai_dm_style import THAI_DM_STYLE
from app.core.config import get_settings
from evals.fixtures import ALL

SYSTEMS = {
    "generate_dm_narration": THAI_DM_STYLE + "\n" + NARRATOR_SYSTEM_EXTRA,
    "generate_session_opening": THAI_DM_STYLE + "\n" + OPENING_SYSTEM,
    "guide_character_creation": CREATION_GUIDE_SYSTEM,
}


async def run(task_filter: str | None) -> None:
    settings = get_settings()
    if settings.llm_provider == "fake":
        raise SystemExit("Set a real REVERIE_LLM_PROVIDER (+key) in backend/.env first.")
    provider = get_provider(settings)

    for group, fixtures in ALL.items():
        if task_filter and group != task_filter:
            continue
        for label, task, context in fixtures:
            messages = [
                {"role": "system", "content": SYSTEMS[task]},
                {"role": "user", "content": context},
            ]
            method = getattr(provider, task)
            result = await method(messages)
            print("=" * 72)
            print(f"[{group}] {label}")
            print("-" * 72)
            print(context)
            print("-" * 72)
            print(result.model_dump_json(indent=2, exclude_none=True))
            print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=list(ALL), default=None)
    args = parser.parse_args()
    asyncio.run(run(args.task))
