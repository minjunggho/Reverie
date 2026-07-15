"""Domain error hierarchy.

These are raised by the engine (services / tabletop / orchestration). The API and
Discord bridge translate them into user-facing responses; they never leak stack
traces to players.
"""
from __future__ import annotations


class ReverieError(Exception):
    """Base class for all engine errors."""


class NotFoundError(ReverieError):
    """A referenced entity does not exist."""


class ConflictError(ReverieError):
    """A uniqueness / optimistic-concurrency conflict."""


class ValidationError(ReverieError):
    """Input or proposed-delta failed engine validation."""


class IllegalStateTransition(ReverieError):
    """A lifecycle / state-machine transition was not allowed."""


class StateIntegrityError(ReverieError):
    """Persisted world/session state is missing or inconsistent.

    Raised instead of silently falling back (e.g. teleporting the party to the
    campaign start). The action stops, the last valid state is preserved, and the
    message names exactly what is missing so the owner can repair it explicitly.
    """


class AuthorizationError(ReverieError):
    """A visibility / retrieval-layer authorization violation."""


class LLMError(ReverieError):
    """The LLM provider failed or returned repeatedly-invalid output."""


class RulesViolation(ValidationError):
    """A proposed action or delta violates the supported rules subset."""
