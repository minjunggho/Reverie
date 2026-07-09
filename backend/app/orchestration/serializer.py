"""Per-session serialized action queue.

MVP policy (documented in `docs/architecture.md`): committed actions for one active
session are processed ONE AT A TIME in arrival order. Correct ordering beats parallel
throughput at a tabletop. We use an in-process `asyncio.Lock` per session — asyncio
wakes lock waiters in FIFO order, so two near-simultaneous `!` actions run in the
order they arrived, and the second reads the state the first committed.

A single bot process is assumed for the MVP. A multi-process deployment would swap
this for a Redis/DB advisory lock; that is deferred and noted in PROGRESS.md.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class SessionSerializer:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    @asynccontextmanager
    async def hold(self, session_id: str):
        lock = self._lock_for(session_id)
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()

    async def run(self, session_id: str, work: Callable[[], Awaitable[T]]) -> T:
        async with self.hold(session_id):
            return await work()
