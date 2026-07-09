"""The ActionCommitment abstraction.

Only `EXPLICIT_PREFIX` is implemented: a message beginning with `!` is a committed
character action. We strip ONLY the `!` marker and preserve the player's original
Thai text verbatim as the action description. This is done deterministically — the
LLM is never consulted to decide whether something is an action.

The abstraction exists so a future, smarter commitment source (AI_INFERRED,
DISCORD_BUTTON, VOICE_CONFIRMED) can be added without touching the pipeline. Those
are intentionally NOT built in the MVP.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.discord_bridge.dto import InboundMessage
from app.models.enums import CommitmentSource

COMMIT_PREFIX = "!"


@dataclass(frozen=True)
class CommittedAction:
    action_text: str                 # original Thai, marker stripped, verbatim
    commitment_source: CommitmentSource


def detect_commitment(inbound: InboundMessage) -> CommittedAction | None:
    """Return a CommittedAction iff the message is an explicit `!` commitment."""
    content = inbound.content
    stripped_left = content.lstrip()
    if not stripped_left.startswith(COMMIT_PREFIX):
        return None
    # Remove exactly the leading marker, then trim surrounding whitespace. The rest
    # of the player's Thai is preserved exactly.
    action_text = stripped_left[len(COMMIT_PREFIX):].strip()
    return CommittedAction(
        action_text=action_text,
        commitment_source=CommitmentSource.EXPLICIT_PREFIX,
    )
