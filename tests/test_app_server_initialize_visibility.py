"""Regression tests for the App Server ``initialize`` visibility surface
(issue #40 Phase 1).

The bug being protected against: until v0.8.1, the JSON-RPC ``initialize``
handshake was bounded by a 600s default timeout and emitted nothing while
waiting. From the user's #40 thread:

    > 10-minute silence is indistinguishable from "agent is genuinely
    > thinking hard" until the failure event lands.

That is the §2 visibility-parity gap. Phase 1 closes it with three
typed-event invariants this test pins:

1. Default initialize budget is 90s (not 600s); env override respected.
2. On success, ``app_server_initialize_completed`` carries ``elapsed_ms``
   and ``prompt_byte_size`` so we can revisit the 90s default with real
   data instead of the single 17s anecdote we have today.
3. While waiting, ``app_server_initialize_progress`` events are emitted
   at the configured cadence (default 30s; 0 disables).
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from claude_anyteam import codex as codex_mod
from claude_anyteam.app_server import AppServerClient


class _StdinWriter:
    def __init__(self, feed_fn) -> None:
        self._feed = feed_fn
        self._closed = False

    def write(self, data: str) -> int:
        if self._closed:
            raise BrokenPipeError("closed")
        self._feed(data)
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self._closed = True


class _QueueReader:
    def __init__(self, q: "queue.Queue[str]") -> None:
        self._q = q

    def __iter__(self):
        while True:
            line = self._q.get()
            if not line:
                return
            yield line

    def readline(self) -> str:
        return self._q.get()

    def close(self) -> None:
        pass


class _FakeProcess:
    def __init__(self, responder, *, delay_s: float = 0.0) -> None:
        self._out_queue: "queue.Queue[str]" = queue.Queue()
        self._responder = responder
        self._delay_s = delay_s
        self.returncode: int | None = None
        self.pid = 99999
        self._stdout_reader = _QueueReader(self._out_queue)
        self._stderr_reader = _QueueReader(queue.Queue())
        self.stdin = _StdinWriter(self._feed)
        self.stdout = self._stdout_reader
        self.stderr = self._stderr_reader

    def _feed(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if self._delay_s > 0:
            # Schedule the response after a delay, on a worker thread.
            def _delayed() -> None:
                import time

                time.sleep(self._delay_s)
                for line in self._responder(msg):
                    self._out_queue.put(line + "\n")

            threading.Thread(target=_delayed, daemon=True).start()
            return
        for line in self._responder(msg):
            self._out_queue.put(line + "\n")

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0
        self._out_queue.put("")

    def kill(self) -> None:
        self.returncode = -9
        self._out_queue.put("")

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0


def _make_client_with(responder, *, delay_s: float = 0.0):
    fake = _FakeProcess(responder, delay_s=delay_s)
    with patch.object(subprocess, "Popen", return_value=fake):
        client = AppServerClient()
        client.start()
    return client, fake


def _captured_emitter():
    captured: list[dict[str, Any]] = []

    def emit(*, kind, severity, summary, payload, visibility=None):
        captured.append(
            {
                "kind": kind,
                "severity": severity,
                "summary": summary,
                "payload": dict(payload),
                "visibility": dict(visibility or {}),
            }
        )
        return None

    return captured, emit


def _ok_responder(msg):
    yield json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"server": "ok"}})


def test_initialize_completed_event_includes_elapsed_and_prompt_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        codex_mod.APP_SERVER_INITIALIZE_PROGRESS_INTERVAL_ENV, raising=False
    )
    monkeypatch.delenv(
        codex_mod.APP_SERVER_INITIALIZE_TIMEOUT_ENV, raising=False
    )

    client, _ = _make_client_with(_ok_responder)
    captured, emit = _captured_emitter()
    try:
        codex_mod._initialize_with_progress_events(
            client,
            emit_visibility=emit,
            prompt_byte_size=2048,
        )
    finally:
        client.close()

    completed = [c for c in captured if c["kind"] == "app_server_initialize_completed"]
    assert len(completed) == 1, f"Expected one completed event, got {captured}"
    payload = completed[0]["payload"]
    assert payload["prompt_byte_size"] == 2048
    assert payload["elapsed_ms"] >= 0
    assert payload["timeout_s"] == 90.0
    assert completed[0]["severity"] == "info"
    assert completed[0]["visibility"]["event_log"] is True
    assert completed[0]["visibility"]["mailbox"] is False


def test_initialize_default_timeout_is_90s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin: the default initialize budget is 90s, not the legacy 600s."""

    monkeypatch.delenv(
        codex_mod.APP_SERVER_INITIALIZE_TIMEOUT_ENV, raising=False
    )

    captured_timeouts: list[float] = []

    class _ProbeClient:
        pid = 1234

        def initialize(self, *, timeout: float | None = None) -> Any:
            captured_timeouts.append(timeout if timeout is not None else -1.0)
            return {"server": "ok"}

    _, emit = _captured_emitter()
    codex_mod._initialize_with_progress_events(
        _ProbeClient(),
        emit_visibility=emit,
        prompt_byte_size=0,
    )

    assert captured_timeouts == [90.0], (
        "Default initialize timeout must be 90s; got "
        f"{captured_timeouts}. Fix the default in codex.py if you intended "
        "to change it — the regression test is here exactly so the change "
        "is deliberate."
    )


def test_initialize_timeout_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin: env override flows through to client.initialize()."""

    monkeypatch.setenv(
        codex_mod.APP_SERVER_INITIALIZE_TIMEOUT_ENV, "12.5"
    )

    captured_timeouts: list[float] = []

    class _ProbeClient:
        pid = 1234

        def initialize(self, *, timeout: float | None = None) -> Any:
            captured_timeouts.append(timeout if timeout is not None else -1.0)
            return {"server": "ok"}

    _, emit = _captured_emitter()
    codex_mod._initialize_with_progress_events(
        _ProbeClient(),
        emit_visibility=emit,
        prompt_byte_size=0,
    )

    assert captured_timeouts == [12.5]


def test_initialize_progress_events_emitted_during_slow_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slow handshake → at least one ``app_server_initialize_progress``
    fires before the success event. Without this the lead has no signal
    distinguishing slow init from a hung process.
    """

    # Tight intervals so the test stays fast: 0.5s budget, 0.1s progress
    # cadence, ~0.3s simulated initialize delay.
    monkeypatch.setenv(
        codex_mod.APP_SERVER_INITIALIZE_TIMEOUT_ENV, "0.5"
    )
    monkeypatch.setenv(
        codex_mod.APP_SERVER_INITIALIZE_PROGRESS_INTERVAL_ENV, "0.1"
    )

    client, _ = _make_client_with(_ok_responder, delay_s=0.3)
    captured, emit = _captured_emitter()
    try:
        codex_mod._initialize_with_progress_events(
            client,
            emit_visibility=emit,
            prompt_byte_size=42,
        )
    finally:
        client.close()

    progress = [c for c in captured if c["kind"] == "app_server_initialize_progress"]
    assert progress, (
        "Expected at least one app_server_initialize_progress event during a "
        f"slow handshake. Got: {[c['kind'] for c in captured]}"
    )
    payload = progress[0]["payload"]
    assert payload["attempt"] >= 1
    assert payload["elapsed_s"] >= 0.0
    assert payload["timeout_s"] == 0.5
    assert payload["progress_interval_s"] == 0.1
    assert payload["prompt_byte_size"] == 42
    # last_observed_pid is best-effort but must be present (even if None on
    # platforms where the fake client doesn't expose it).
    assert "last_observed_pid" in payload

    completed = [c for c in captured if c["kind"] == "app_server_initialize_completed"]
    assert len(completed) == 1


def test_initialize_progress_disabled_when_interval_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin: setting the progress interval to 0 disables emission entirely.
    Operators who don't want the periodic noise can opt out cleanly.
    """

    monkeypatch.setenv(
        codex_mod.APP_SERVER_INITIALIZE_TIMEOUT_ENV, "0.5"
    )
    monkeypatch.setenv(
        codex_mod.APP_SERVER_INITIALIZE_PROGRESS_INTERVAL_ENV, "0"
    )

    client, _ = _make_client_with(_ok_responder, delay_s=0.3)
    captured, emit = _captured_emitter()
    try:
        codex_mod._initialize_with_progress_events(
            client,
            emit_visibility=emit,
            prompt_byte_size=0,
        )
    finally:
        client.close()

    progress = [c for c in captured if c["kind"] == "app_server_initialize_progress"]
    assert progress == [], (
        "Setting interval=0 must disable progress emission entirely. "
        f"Got: {progress}"
    )

    # Success event still fires.
    completed = [c for c in captured if c["kind"] == "app_server_initialize_completed"]
    assert len(completed) == 1


def test_initialize_timeout_propagates_underlying_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When initialize times out, the wrapper must re-raise the App Server
    error rather than swallowing it. Loop-side handlers key on the error
    message ("did not respond to initialize") to route to typed
    task_blocked instead of prose_reply.
    """

    monkeypatch.setenv(
        codex_mod.APP_SERVER_INITIALIZE_TIMEOUT_ENV, "0.2"
    )
    monkeypatch.setenv(
        codex_mod.APP_SERVER_INITIALIZE_PROGRESS_INTERVAL_ENV, "0"
    )

    def never_responds(_msg):
        return iter([])

    client, _ = _make_client_with(never_responds)
    _, emit = _captured_emitter()
    try:
        from claude_anyteam.app_server import AppServerError

        with pytest.raises(AppServerError, match="did not respond to initialize"):
            codex_mod._initialize_with_progress_events(
                client,
                emit_visibility=emit,
                prompt_byte_size=0,
            )
    finally:
        client.close()
