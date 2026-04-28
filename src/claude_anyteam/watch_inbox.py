"""Event-driven inbox wakeups for routed teammates.

The team protocol stores one JSON inbox file per teammate.  The control loops
used to poll that file with a fixed sleep, which imposed a backend-independent
latency floor on peer DMs.  This helper keeps a lightweight watch on the inbox
directory and wakes only for the current teammate's inbox basename, matching the
thundering-herd guard used by aproto-codex-bridge's ``fs.watch`` loop.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from claude_teams import messaging as _messaging  # type: ignore[import-untyped]

from . import logger

ACTIVE_WAIT_S = 0.1
IDLE_WAIT_S = 2.0
FLUSH_DEBOUNCE_MS = 50
WATCH_STEP_MS = 10
WATCH_RUST_TIMEOUT_MS = 1000
WATCH_POLL_DELAY_MS = 50

WatchFunc = Callable[..., Iterable[set[tuple[Any, str]]]]


def inbox_path(team: str, agent_name: str) -> Path:
    """Return the protocol inbox path for ``agent_name``."""

    return _messaging.inbox_path(team, agent_name)


def _import_watch() -> WatchFunc | None:
    try:
        from watchfiles import watch
    except Exception as exc:  # pragma: no cover - exercised via injected fallback
        logger.warn("inbox_watch.import_unavailable", error=str(exc))
        return None
    return watch


class WatchInbox:
    """Wake on changes to one teammate inbox file, with sleep fallback.

    ``wait_for_change(timeout_s)`` returns ``True`` when the watched inbox file
    changed before the timeout and ``False`` when the call timed out.  If the
    watcher cannot be installed or later fails, the method degrades to a bounded
    sleep using ``fallback_timeout_s`` (normally the existing ``poll_interval_s``)
    so callers preserve their old polling behavior.
    """

    def __init__(
        self,
        inbox_file: str | Path,
        *,
        fallback_timeout_s: float | None = None,
        watch_func: WatchFunc | None = None,
    ) -> None:
        self.inbox_file = Path(inbox_file)
        self.inbox_dir = self.inbox_file.parent
        self.inbox_basename = self.inbox_file.name
        self.fallback_timeout_s = fallback_timeout_s
        self._watch_func = watch_func if watch_func is not None else _import_watch()
        self._change_event = threading.Event()
        self._stop_event = threading.Event()
        self._disabled_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._disabled_reason: str | None = None

        if self._watch_func is None:
            self._disable("watchfiles unavailable")
            return
        if not self.inbox_dir.exists():
            self._disable(f"inbox directory does not exist: {self.inbox_dir}")
            return
        self._thread = threading.Thread(
            target=self._run,
            name=f"claude-anyteam-watch-{self.inbox_basename}",
            daemon=True,
        )
        self._thread.start()

    @classmethod
    def for_team(
        cls,
        team: str,
        agent_name: str,
        *,
        fallback_timeout_s: float | None = None,
    ) -> "WatchInbox":
        return cls(
            inbox_path(team, agent_name),
            fallback_timeout_s=fallback_timeout_s,
        )

    @property
    def active(self) -> bool:
        return not self._disabled_event.is_set()

    @property
    def disabled_reason(self) -> str | None:
        return self._disabled_reason

    def close(self) -> None:
        self._stop_event.set()
        self._change_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.2)

    def wait_for_change(self, timeout_s: float) -> bool:
        """Wait until this inbox file changes or ``timeout_s`` elapses."""

        timeout_s = max(0.0, float(timeout_s))
        if self._disabled_event.is_set():
            time.sleep(self._fallback_sleep_s(timeout_s))
            return False

        baseline = self._stat_signature()
        start = time.monotonic()
        deadline = start + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if self._change_event.wait(timeout=min(remaining, 0.05)):
                self._change_event.clear()
                return True
            current = self._stat_signature()
            if current != baseline:
                return True
            if self._disabled_event.is_set():
                elapsed = time.monotonic() - start
                fallback_remaining = max(0.0, self._fallback_sleep_s(timeout_s) - elapsed)
                if fallback_remaining:
                    time.sleep(fallback_remaining)
                return False

    def _fallback_sleep_s(self, requested_timeout_s: float) -> float:
        if self.fallback_timeout_s is None:
            return requested_timeout_s
        return max(0.0, float(self.fallback_timeout_s))

    def _disable(self, reason: str) -> None:
        if not self._disabled_event.is_set():
            self._disabled_reason = reason
            self._disabled_event.set()
            logger.warn(
                "inbox_watch.disabled",
                inbox=str(self.inbox_file),
                reason=reason,
            )

    def _matches_inbox(self, _change: Any, changed_path: str) -> bool:
        return Path(changed_path).name == self.inbox_basename

    def _stat_signature(self) -> tuple[int, int] | None:
        try:
            stat = self.inbox_file.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _run(self) -> None:
        assert self._watch_func is not None
        try:
            for changes in self._watch_func(
                self.inbox_dir,
                watch_filter=self._matches_inbox,
                debounce=FLUSH_DEBOUNCE_MS,
                step=WATCH_STEP_MS,
                stop_event=self._stop_event,
                rust_timeout=WATCH_RUST_TIMEOUT_MS,
                yield_on_timeout=False,
                raise_interrupt=False,
                recursive=False,
                poll_delay_ms=WATCH_POLL_DELAY_MS,
            ):
                if self._stop_event.is_set():
                    return
                if changes:
                    self._change_event.set()
        except Exception as exc:
            self._disable(str(exc))


def adaptive_wait_s(*, saw_messages: bool) -> float:
    return ACTIVE_WAIT_S if saw_messages else IDLE_WAIT_S
