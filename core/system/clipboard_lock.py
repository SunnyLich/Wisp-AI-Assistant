"""Cross-process clipboard lock for save/restore selection capture.

Selection capture's fallback path synthesizes a copy keystroke and does a
clipboard save->copy->restore dance. Two processes doing that concurrently
(the Wisp app and the MCP context server, both of which import this code)
can restore each other's stale clipboard. This advisory file lock serializes
those critical sections across processes.

Same OS-level advisory-lock pattern as single_instance.py: the OS releases
the lock on process exit or crash, so there are no stale locks to clean up.
The lock file lives in the user-data dir so every launch flavor (dev run,
installed build, standalone context server) contends for the *same* lock.

Fail-open by design: a capture must never break because of lock trouble, so
after `timeout` seconds of contention (or on any filesystem error) the caller
proceeds without the lock — no worse than before the lock existed.
"""
from __future__ import annotations

import contextlib
import logging
import sys
import time

from core.system.paths import USER_DATA_DIR

log = logging.getLogger("wisp.clipboard_lock")

CLIPBOARD_LOCK_FILE = USER_DATA_DIR / "wisp_clipboard.lock"

_POLL_SECONDS = 0.02


def _try_lock(fh) -> bool:
    """Attempt one non-blocking lock on an open handle; True when acquired."""
    if sys.platform == "win32":
        import msvcrt

        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fh) -> None:
    """Release a lock acquired by _try_lock (best effort)."""
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def held(timeout: float = 2.0):
    """Hold the cross-process clipboard lock for the duration of the block.

    Yields True when the lock was acquired, False when the block proceeds
    unlocked (contention past `timeout`, or the lock file was unusable).
    """
    try:
        CLIPBOARD_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = open(CLIPBOARD_LOCK_FILE, "a+")
    except OSError:
        log.warning("Could not open clipboard lock file; capturing unlocked.")
        yield False
        return

    acquired = False
    try:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            acquired = _try_lock(fh)
            if acquired or time.monotonic() >= deadline:
                break
            time.sleep(_POLL_SECONDS)
        if not acquired:
            log.warning("Clipboard lock contended past %.1fs; capturing unlocked.", timeout)
        yield acquired
    finally:
        if acquired:
            _unlock(fh)
        fh.close()
