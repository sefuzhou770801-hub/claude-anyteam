"""Regression test for AppServerClient process-group teardown (issue #40).

Issue #40 — concurrent codex-* spawn deadlock: the second-or-later codex
app-server cold start within the same long-lived wrapper process hangs at
JSON-RPC ``initialize`` for the full 600s timeout while burning ~100% CPU.
The wrapper's first ``client.close()`` SIGTERMs only the codex-app-server
leader; helper subprocesses Codex forked for auth refresh / model I/O /
network handshake survived the close because they were in the parent's
process group. On the next per-turn ``AppServerClient`` cold start they
collided with the new process's open fds / sockets / cache locks.

The fix puts each ``AppServerClient``'s subprocess tree in its own POSIX
session (``start_new_session=True``) and SIGTERMs the entire process group
on close (``terminate_process_group=True``). This test pins both flags so
a future refactor cannot silently revert to the leak-on-close mode in #40.

Mirrors the convention the gemini ACP transport already uses
(``backends/gemini/acp.py``: ``client.terminate_process_group(...)``).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Any
from unittest.mock import patch

import pytest

from claude_anyteam.app_server import AppServerClient


def test_app_server_client_starts_in_new_session() -> None:
    """``start_new_session=True`` must be passed to subprocess.Popen so the
    Codex app-server's helper subprocesses live in their own process group,
    not the wrapper's. Required for ``terminate_process_group`` to
    distinguish "this turn's tree" from "the wrapper itself."
    """

    captured: dict[str, Any] = {}

    class _StoppedReader:
        def __iter__(self):
            if False:
                yield  # pragma: no cover

        def readline(self) -> str:
            return ""

        def close(self) -> None:
            pass

    class _Sink:
        def __init__(self) -> None:
            self._closed = False

        def write(self, data: str) -> int:
            return len(data)

        def flush(self) -> None:
            pass

        def close(self) -> None:
            self._closed = True

    class _FakeProcess:
        def __init__(self) -> None:
            self.pid = 4242
            self.returncode: int | None = None
            self.stdin = _Sink()
            self.stdout = _StoppedReader()
            self.stderr = _StoppedReader()

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = -9

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode or 0

    def fake_popen(*args: Any, **kwargs: Any) -> _FakeProcess:
        captured.update(kwargs)
        return _FakeProcess()

    with patch.object(subprocess, "Popen", side_effect=fake_popen):
        client = AppServerClient()
        client.start()
        try:
            assert captured.get("start_new_session") is True, (
                "AppServerClient must launch with start_new_session=True so "
                "the codex-app-server subprocess tree lives in its own POSIX "
                "session — see issue #40."
            )
        finally:
            client.close(timeout=0.1)


def test_app_server_client_terminate_process_group_flag_set() -> None:
    """``terminate_process_group=True`` must propagate to ``close()`` so
    SIGTERM goes to the whole tree, not just the leader.
    """

    client = AppServerClient()
    assert client._terminate_process_group is True, (
        "AppServerClient must opt into process-group termination so "
        "client.close() reaps codex-app-server's helper subprocesses — "
        "see issue #40."
    )


@pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("CI") == "true",
    reason="real-process integration test; runs on developer Linux only",
)
def test_app_server_close_signals_helper_children() -> None:
    """End-to-end pin: launch a tiny shell script that forks a long-lived
    helper child, then assert the helper dies when the leader is
    ``close()``'d. Without process-group-aware teardown, the helper
    survives — which is the leak that produces the #40 hang on the next
    cold start.

    Skipped in CI / non-Linux to keep the deterministic suite hermetic;
    run locally with ``pytest tests/test_app_server_process_group.py
    -k close_signals --no-header``.
    """

    # Minimal "app-server" that:
    #   1. Forks a sleeping helper child (writes its PID to stdout for us).
    #   2. Reads from stdin so close()'s stdin-EOF can wake the leader.
    leader_argv = [
        "bash",
        "-c",
        # Run the helper in the background, print its PID, then read until EOF.
        "sleep 600 & echo $! ; cat >/dev/null",
    ]

    proc = subprocess.Popen(
        leader_argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    try:
        assert proc.stdout is not None
        helper_pid = int(proc.stdout.readline().strip())
        # Helper alive (sanity).
        os.kill(helper_pid, 0)

        # Mimic AppServerClient close: SIGTERM the *process group*.
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)

        # Give the kernel a moment to reap the helper.
        for _ in range(50):
            try:
                os.kill(helper_pid, 0)
            except ProcessLookupError:
                break
            else:
                import time

                time.sleep(0.05)

        with pytest.raises(ProcessLookupError):
            os.kill(helper_pid, 0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
