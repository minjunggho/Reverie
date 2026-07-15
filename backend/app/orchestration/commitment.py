"""The ActionCommitment abstraction.

Only `EXPLICIT_PREFIX` is implemented: a message beginning with `!` is a committed
character action. We strip ONLY the `!` marker and preserve the player's original
Thai text verbatim as the action description. This is done deterministically — the
LLM is never consulted to decide whether something is an action.

Speech vs. action is also decided deterministically here: a `!` message whose
remaining text is wrapped in quotes — `!"..."` — is the character SPEAKING those
exact words, not performing an action. The distinction removes a whole class of
hallucination (the interpreter guessing whether a line is talk or deed, or the
narrator rewording what was said): quoted text is carried verbatim to the dialogue
path and is never executed as a physical action.

The abstraction exists so a future, smarter commitment source (AI_INFERRED,
DISCORD_BUTTON, VOICE_CONFIRMED) can be added without touching the pipeline. Those
are intentionally NOT built in the MVP.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.discord_bridge.dto import InboundMessage
from app.models.enums import CommitmentSource

COMMIT_PREFIX = "!"

# Opening→closing quote pairs that mark a committed message as SPEECH. The straight
# ASCII pair is the documented form; the curly/guillemet/CJK variants are included
# so a phone IME or autocorrect that "improves" the player's quotes still registers
# as speech rather than silently becoming an action.
_SPEECH_QUOTES = {
    '"': '"',
    "“": "”",  # “ ”
    "«": "»",  # « »
    "「": "」",  # 「 」
    "„": "”",  # „ ”
}


@dataclass(frozen=True)
class CommittedAction:
    action_text: str                 # original Thai, marker (and speech quotes) stripped
    commitment_source: CommitmentSource
    is_speech: bool = False          # True iff the player wrapped the text in quotes


def detect_commitment(inbound: InboundMessage) -> CommittedAction | None:
    """Return a CommittedAction iff the message is an explicit `!` commitment.

    `!"..."` (fully quoted) → speech: the quoted words are the action_text, verbatim.
    `!...` (anything else) → a physical/mechanical action, exactly as before.
    """
    content = inbound.content
    stripped_left = content.lstrip()
    if not stripped_left.startswith(COMMIT_PREFIX):
        return None
    # Remove exactly the leading marker, then trim surrounding whitespace. The rest
    # of the player's Thai is preserved exactly.
    body = stripped_left[len(COMMIT_PREFIX):].strip()
    spoken = _quoted_speech(body)
    if spoken is not None:
        return CommittedAction(
            action_text=spoken,
            commitment_source=CommitmentSource.EXPLICIT_PREFIX,
            is_speech=True,
        )
    return CommittedAction(
        action_text=body,
        commitment_source=CommitmentSource.EXPLICIT_PREFIX,
        is_speech=False,
    )


def _quoted_speech(body: str) -> str | None:
    """If `body` is fully wrapped in a supported quote pair, return the verbatim
    inner text (surrounding quotes and whitespace stripped). Otherwise return None.

    Only a WHOLE-message wrap counts, so `!"เปิดประตู"` is speech but `!ผลักประตูแล้ว
    ตะโกน "หยุด"` stays an action with an embedded quote."""
    if len(body) < 2:
        return None
    closing = _SPEECH_QUOTES.get(body[0])
    if closing is None or body[-1] != closing:
        return None
    inner = body[1:-1].strip()
    return inner or None
