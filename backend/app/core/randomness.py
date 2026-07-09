"""Randomness abstraction.

ALL authoritative randomness in the engine goes through a `Randomness` instance.
Production uses `SystemRandomness`; tests inject `SequenceRandomness` to make dice
deterministic. The LLM has no access to this object — that is how we structurally
guarantee the model can never produce an authoritative die result.
"""
from __future__ import annotations

import random
from abc import ABC, abstractmethod
from collections import deque
from typing import Iterable


class Randomness(ABC):
    @abstractmethod
    def roll(self, sides: int) -> int:
        """Return an integer in [1, sides]."""

    def randint(self, a: int, b: int) -> int:
        """Return an integer in [a, b] inclusive (default: derive from roll)."""
        span = b - a + 1
        if span <= 0:
            raise ValueError("randint requires b >= a")
        return a + (self.roll(span) - 1)


class SystemRandomness(Randomness):
    """Real randomness for production."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def roll(self, sides: int) -> int:
        if sides < 1:
            raise ValueError("sides must be >= 1")
        return self._rng.randint(1, sides)

    def randint(self, a: int, b: int) -> int:
        return self._rng.randint(a, b)


class SequenceRandomness(Randomness):
    """Deterministic randomness for tests.

    Pops queued values from `results` on each `roll`. A queued value must be a valid
    face for the requested die (1..sides), otherwise it raises loudly so a
    mis-scripted test fails visibly rather than silently. When the queue is
    exhausted it raises unless `default` is provided.
    """

    def __init__(self, results: Iterable[int] = (), default: int | None = None) -> None:
        self._queue: deque[int] = deque(results)
        self._default = default

    def push(self, *values: int) -> "SequenceRandomness":
        self._queue.extend(values)
        return self

    def roll(self, sides: int) -> int:
        if sides < 1:
            raise ValueError("sides must be >= 1")
        if self._queue:
            value = self._queue.popleft()
        elif self._default is not None:
            value = self._default
        else:
            raise RuntimeError(
                "SequenceRandomness exhausted: no queued result and no default"
            )
        if not (1 <= value <= sides):
            raise ValueError(
                f"scripted roll {value} is not a valid face for a d{sides}"
            )
        return value
