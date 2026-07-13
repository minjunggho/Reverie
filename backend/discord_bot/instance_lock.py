"""Host-local ownership lock for one live Discord client per bot token."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import BinaryIO


class DuplicateBotInstanceError(RuntimeError):
    """Another process on this host already owns this Discord bot token."""


class BotInstanceLock:
    """Non-blocking OS file lock keyed by a one-way digest of the bot token.

    The file may remain after shutdown, but the OS lock never does.  Keeping the
    file avoids an unlink/acquire race and no token or reversible credential is
    written to disk.
    """

    def __init__(self, token: str, *, directory: Path | None = None) -> None:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
        lock_dir = directory or Path(tempfile.gettempdir())
        self.path = lock_dir / f"reverie-discord-{digest}.lock"
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            _lock(handle)
        except OSError as exc:
            handle.close()
            raise DuplicateBotInstanceError(
                "Another Reverie Discord bot process is already using this token "
                "on this host. Stop the older process before starting a new one."
            ) from exc
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        handle, self._handle = self._handle, None
        try:
            _unlock(handle)
        finally:
            handle.close()


if os.name == "nt":
    import msvcrt

    def _lock(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
