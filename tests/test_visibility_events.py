from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]

from claude_anyteam import app_server as app_server_mod
from claude_anyteam import codex as codex_mod
from claude_anyteam import protocol_io as pio
from claude_anyteam.messages import VisibilityEvent


@pytest.fixture
def events_root(tmp_path: Path, monkeypatch):
    base = tmp_path / "home" / ".claude" / "teams"
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", base)
    return base


def _event(kind: str, seq: int, **overrides) -> VisibilityEvent:
    data = {
        "kind": kind,
        "event_id": f"agent:turn:{seq:06d}",
        "team": "team-x",
        "agent": "codex-runtime",
        "backend": "codex_app_server",
        "task_id": "16",
        "turn_id": "turn-1",
        "seq": seq,
        "severity": "info",
        "summary": f"event {seq}",
        "payload": {},
    }
    data.update(overrides)
    return VisibilityEvent.model_validate(data)


class _FakeClient:
    notifications = None

    def __init__(self, *args, notifications: list[dict] | None = None, **kwargs):
        self.notifications = _NotificationQueue(notifications or [])
        self.steers: list[dict] = []

    def start(self):
        pass

    def initialize(self):
        return {}

    def thread_start(self, **kwargs):
        return "thread-1"

    def turn_start(self, **kwargs):
        return "turn-1"

    def turn_steer(self, **kwargs):
        self.steers.append(kwargs)
        return kwargs["expected_turn_id"]

    def turn_interrupt(self, **kwargs):
        pass

    def close(self):
        pass


class _NotificationQueue:
    def __init__(self, items: list[dict]):
        self.items = list(items)

    def get(self, timeout=None):
        if self.items:
            item = self.items.pop(0)
            if item == {"__raise__": True}:
                raise RuntimeError("empty (test)")
            return item
        raise RuntimeError("empty (test)")


def test_append_visibility_events_reads_since_seq(events_root: Path):
    for seq in range(1, 101):
        pio.append_visibility_event("team-x", "codex-runtime", _event("turn_progress", seq))

    got = pio.read_visibility_events("team-x", "codex-runtime", since_seq=50)
    assert [e.seq for e in got] == list(range(51, 101))
    assert (events_root / "team-x" / "events" / ".lock").exists()


def test_append_visibility_event_validates_before_write(events_root: Path):
    with pytest.raises(Exception):
        pio.append_visibility_event(
            "team-x",
            "codex-runtime",
            {
                "kind": "not_a_kind",
                "event_id": "bad",
                "team": "team-x",
                "agent": "codex-runtime",
                "backend": "codex_app_server",
                "seq": 1,
                "severity": "info",
                "summary": "bad",
                "payload": {},
            },
        )
    assert not pio.visibility_event_path("team-x", "codex-runtime").exists()


def test_send_visibility_event_to_lead_sets_message_kind(events_root: Path):
    event = _event(
        "turn_progress",
        1,
        visibility={"mailbox": True, "task_state": True, "event_log": True, "stderr": True},
    )
    pio.send_visibility_event_to_lead("team-x", "codex-runtime", event)

    raw = json.loads((events_root / "team-x" / "inboxes" / "team-lead.json").read_text())
    assert raw[0]["messageKind"] == "turn_progress"
    body = json.loads(raw[0]["text"])
    assert body["kind"] == "turn_progress"
    assert body["payload"] == {}


def test_app_server_command_execution_writes_tool_event_to_event_log(events_root: Path):
    notifications = [
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "commandExecution",
                    "command": "uv run pytest tests/test_visibility_events.py",
                    "exitCode": 0,
                    "durationMs": 1234,
                    "stdoutPreview": "1 passed",
                }
            },
        },
        {"method": "turn/completed", "params": {"turn": {"status": "ok"}}},
    ]

    def make_client(*args, **kwargs):
        return _FakeClient(*args, notifications=notifications, **kwargs)

    with patch.object(app_server_mod, "AppServerClient", make_client):
        result = codex_mod.app_server_invoke(
            task_prompt="run tests",
            cwd=Path("/tmp"),
            schema=None,
            settings_team="team-x",
            settings_agent="codex-runtime",
            task_id="16",
            event_sink=lambda event: pio.append_visibility_event(
                "team-x", "codex-runtime", event
            ),
        )

    assert result.exit_code == 0
    events = pio.read_visibility_events("team-x", "codex-runtime")
    tool_events = [e for e in events if e.kind == "tool_event"]
    assert len(tool_events) == 1
    payload = tool_events[0].payload
    assert payload["category"] == "host_tool"
    assert payload["tool_name"] == "commandExecution"
    assert payload["raw_backend_type"] == "commandExecution"
    assert payload["target"] == "uv run pytest tests/test_visibility_events.py"
    assert payload["exit_code"] == 0
    assert all(e.visibility.event_log for e in events)
    assert not any(e.visibility.stderr and not e.visibility.event_log for e in events)


def test_app_server_no_checkpoint_after_300s_emits_turn_progress(monkeypatch):
    notifications = [
        {"__raise__": True},
        {"method": "turn/completed", "params": {"turn": {"status": "ok"}}},
    ]
    created: list[_FakeClient] = []

    def make_client(*args, **kwargs):
        client = _FakeClient(*args, notifications=notifications, **kwargs)
        created.append(client)
        return client

    ticks = iter([0, 0, 301, 301, 301, 301, 302, 303])

    def fake_monotonic():
        try:
            return next(ticks)
        except StopIteration:
            return 303

    emitted: list[VisibilityEvent] = []
    with (
        patch.object(app_server_mod, "AppServerClient", make_client),
        monkeypatch.context() as m,
    ):
        m.setattr(codex_mod.time, "monotonic", fake_monotonic)
        result = codex_mod.app_server_invoke(
            task_prompt="long task",
            cwd=Path("/tmp"),
            schema=None,
            settings_team="team-x",
            settings_agent="codex-runtime",
            task_id="16",
            overall_timeout_s=900,
            non_progress_warn_s=300,
            event_sink=emitted.append,
        )

    assert result.exit_code == 0
    progress = [e for e in emitted if e.kind == "turn_progress" and e.severity == "warn"]
    assert len(progress) == 1
    assert progress[0].payload["elapsed_s"] == 301
    assert progress[0].payload["action_taken"] == "turn_steer_sent"
    assert progress[0].visibility.mailbox is True
    assert progress[0].visibility.task_state is True
    assert created[0].steers, "watchdog should send checkpoint turn/steer"
