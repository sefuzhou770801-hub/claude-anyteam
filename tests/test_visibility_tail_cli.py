from __future__ import annotations

import io
import threading
import time
from pathlib import Path

import pytest
from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]

from claude_anyteam import cli
from claude_anyteam import protocol_io as pio
from claude_anyteam import visibility_tail
from claude_anyteam.messages import VisibilityEvent


@pytest.fixture
def teams_root(tmp_path: Path, monkeypatch):
    base = tmp_path / "teams"
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", base)
    return base


def _event(
    kind: str,
    seq: int,
    *,
    agent: str = "codex-a",
    backend: str = "codex_exec",
    severity: str = "info",
    summary: str | None = None,
    payload: dict | None = None,
) -> VisibilityEvent:
    return VisibilityEvent.model_validate(
        {
            "kind": kind,
            "event_id": f"{agent}:turn-1:{seq:06d}",
            "timestamp": f"2026-04-27T15:30:0{seq}.000Z",
            "team": "team-x",
            "agent": agent,
            "backend": backend,
            "task_id": "31",
            "turn_id": "turn-1",
            "seq": seq,
            "severity": severity,
            "summary": summary or f"event {seq}",
            "payload": payload or {},
        }
    )


def test_visibility_tail_golden_tricard_output(teams_root: Path):
    pio.append_event(
        "team-x",
        "codex-a",
        _event(
            "turn_started",
            1,
            summary="codex turn started",
            payload={
                "mode": "task",
                "prompt_kind": "task_complete",
                "cwd": "/repo",
                "timeout_s": 900,
                "model": "gpt-5.5",
                "effort": "high",
            },
        ),
    )
    pio.append_event(
        "team-x",
        "codex-a",
        _event(
            "tool_event",
            2,
            summary="read_file: src/app.py",
            payload={
                "category": "host_tool",
                "tool_name": "read_file",
                "phase": "completed",
                "target": "src/app.py",
                "status": "success",
                "exit_code": 0,
                "duration_ms": 12,
            },
        ),
    )
    pio.append_event(
        "team-x",
        "codex-a",
        _event(
            "turn_failed",
            3,
            severity="error",
            summary="codex turn failed",
            payload={
                "exit_code": 124,
                "elapsed_s": 900.0,
                "structured": False,
                "event_count": 2,
                "tool_call_events": 1,
                "error_class": "turn_timeout",
                "error": "codex exec timed out after 900s",
            },
        ),
    )

    out = io.StringIO()
    err = io.StringIO()
    rc = visibility_tail.main(
        ["--team", "team-x", "--from-start", "--no-follow", "--no-color"],
        stdout=out,
        stderr=err,
    )

    assert rc == 0
    assert err.getvalue() == ""
    assert out.getvalue() == (
        "2026-04-27T15:30:01.000Z seq=1 codex-a codex_exec INFO turn_started "
        "[ARGS] mode=task prompt=task_complete cwd=/repo timeout_s=900 model=gpt-5.5 effort=high "
        "[RESULT] started :: codex turn started\n"
        "2026-04-27T15:30:02.000Z seq=2 codex-a codex_exec INFO tool_event "
        "[ARGS] tool=read_file category=host_tool phase=completed args=src/app.py "
        "[RESULT] status=success exit_code=0 duration_ms=12 :: read_file: src/app.py\n"
        "2026-04-27T15:30:03.000Z seq=3 codex-a codex_exec ERROR turn_failed "
        "[ARGS] task_id=31 turn_id=turn-1 "
        "[RESULT] exit_code=124 elapsed_s=900.0 structured=false events=2 tool_calls=1 "
        "[ERROR] class=turn_timeout error=\"codex exec timed out after 900s\" :: codex turn failed\n"
    )


def test_append_event_mirrors_rows_to_per_team_visibility_stream(teams_root: Path):
    event = _event("tool_event", 1, summary="mirrored")

    pio.append_event("team-x", "codex-a", event)

    aggregate = pio.team_visibility_event_path("team-x")
    per_agent = pio.visibility_event_path("team-x", "codex-a")
    assert aggregate.exists()
    assert per_agent.exists()
    assert aggregate.read_text(encoding="utf-8") == per_agent.read_text(
        encoding="utf-8"
    )
    assert '"summary":"mirrored"' in aggregate.read_text(encoding="utf-8")


def test_visibility_tail_defaults_to_attach_at_current_eof(teams_root: Path):
    pio.append_event(
        "team-x",
        "codex-a",
        _event("turn_started", 1, summary="old event"),
    )

    out = io.StringIO()
    err = io.StringIO()
    result: dict[str, int] = {}

    def _run() -> None:
        result["rc"] = visibility_tail.main(
            [
                "--team",
                "team-x",
                "--no-color",
                "--max-events",
                "1",
                "--poll-s",
                "0.01",
            ],
            stdout=out,
            stderr=err,
        )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    time.sleep(0.1)
    pio.append_event(
        "team-x",
        "codex-a",
        _event("turn_completed", 2, summary="new event", payload={"exit_code": 0}),
    )
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert result["rc"] == 0
    assert err.getvalue() == ""
    rendered = out.getvalue()
    assert "seq=1" not in rendered
    assert "old event" not in rendered
    assert "seq=2" in rendered
    assert "new event" in rendered


def test_visibility_tail_filters_agent_and_dispatches_from_top_level(
    teams_root: Path,
    capsys,
):
    pio.append_event("team-x", "codex-a", _event("turn_started", 1, agent="codex-a"))
    pio.append_event("team-x", "gemini-a", _event("turn_started", 1, agent="gemini-a"))

    rc = cli.main(
        [
            "visibility-tail",
            "--team",
            "team-x",
            "--agent",
            "gemini-a",
            "--from-start",
            "--no-follow",
            "--no-color",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert "gemini-a" in captured.out
    assert "codex-a" not in captured.out
