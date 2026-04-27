from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path

from filelock import FileLock

DEFAULT_FILE_LOCK_TIMEOUT_S = 30.0
FILE_LOCK_TIMEOUT_ENV = "CLAUDE_ANYTEAM_FILELOCK_TIMEOUT_S"
LEGACY_FILE_LOCK_TIMEOUT_ENV = "CLAUDE_TEAMS_FILELOCK_TIMEOUT_S"


def _default_timeout_s() -> float:
    raw = os.environ.get(FILE_LOCK_TIMEOUT_ENV) or os.environ.get(LEGACY_FILE_LOCK_TIMEOUT_ENV)
    if raw is None or raw == "":
        return DEFAULT_FILE_LOCK_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_FILE_LOCK_TIMEOUT_S
    return max(0.0, value)


@contextmanager
def file_lock(lock_path: Path, timeout: float | None = None):
    """Acquire a filesystem lock with a bounded wait.

    R4: default to 30s (overridable with
    `CLAUDE_ANYTEAM_FILELOCK_TIMEOUT_S`) so a crashed teammate or stale lock
    holder cannot deadlock later adapter startups forever. Callers that need a
    shorter probe can pass `timeout=` directly.
    """

    effective_timeout = _default_timeout_s() if timeout is None else timeout
    with FileLock(str(lock_path), timeout=effective_timeout):
        yield
