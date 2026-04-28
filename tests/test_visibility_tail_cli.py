from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import io
import json
import threading
import time
from pathlib import Path

import pytest
from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]

from claude_anyteam import cli
from claude_anyteam import protocol_io as pio
from claude_anyteam import visibility_tail
from claude_anyteam.messages import VisibilityEvent
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve


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


@asynccontextmanager
async def _visibility_ws_server(
    team: str,
    *,
    agent: str | None = None,
    filter_kinds: set[str] | None = None,
    since=None,
    from_start: bool = False,
    poll_s: float = 0.01,
):
    err = io.StringIO()
    hub = visibility_tail._VisibilityFanoutHub(
        team=team,
        from_start=from_start,
        poll_s=poll_s,
        stderr=err,
    )
    await hub.start()

    async def handler(websocket):
        await visibility_tail._visibility_ws_handler(
            websocket,
            hub=hub,
            subscription=visibility_tail._base_subscription(
                agent=agent,
                filter_kinds=filter_kinds,
                since=since,
            ),
            allow_remote=False,
        )

    try:
        async with serve(handler, "127.0.0.1", 0) as server:
            assert server.sockets is not None
            host, port = server.sockets[0].getsockname()[:2]
            yield f"ws://{host}:{port}", err
    finally:
        await hub.stop()


async def _recv_event(websocket, *, timeout: float = 2.0) -> dict:
    while True:
        row = json.loads(await asyncio.wait_for(websocket.recv(), timeout=timeout))
        if "event_id" in row:
            return row


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


def test_visibility_tail_expands_long_payload_cards(teams_root: Path):
    pio.append_event(
        "team-x",
        "codex-a",
        _event(
            "tool_event",
            4,
            severity="error",
            summary="shell failed",
            payload={
                "category": "host_tool",
                "tool_name": "shell",
                "phase": "completed",
                "tool_args": {
                    "cmd": "python - <<'PY'\nprint('alpha')\nPY\n"
                    + ("x" * 140)
                },
                "status": "failed",
                "exit_code": 1,
                "duration_ms": 42,
                "stdout_preview": "first line\nsecond line\n" + ("z" * 130),
                "stderr_preview": "Traceback (most recent call last)\n" + ("boom " * 45),
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
    rendered = out.getvalue()
    assert rendered.startswith(
        "2026-04-27T15:30:04.000Z seq=4 codex-a codex_exec ERROR tool_event :: shell failed\n"
    )
    assert "\n  [ARGS]\n" in rendered
    assert "\n    args:\n" in rendered
    assert '"cmd":' in rendered
    assert "\n  [RESULT]\n" in rendered
    assert "\n    stdout:\n" in rendered
    assert "first line" in rendered
    assert "\n  [ERROR]\n" in rendered
    assert "\n    error:\n" in rendered
    assert "Traceback (most recent call last)" in rendered
    assert len(rendered.splitlines()) > 12


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


def test_visibility_tail_json_filters_kind_and_since(teams_root: Path):
    pio.append_event(
        "team-x",
        "codex-a",
        _event("turn_started", 1, summary="too old"),
    )
    pio.append_event(
        "team-x",
        "codex-a",
        _event("tool_event", 2, summary="matching tool", payload={"tool_name": "read_file"}),
    )
    pio.append_event(
        "team-x",
        "codex-a",
        _event("turn_completed", 3, summary="wrong kind", payload={"exit_code": 0}),
    )

    out = io.StringIO()
    err = io.StringIO()
    rc = visibility_tail.main(
        [
            "--team",
            "team-x",
            "--from-start",
            "--no-follow",
            "--json",
            "--filter-kind",
            "tool_event,turn_failed",
            "--since",
            "2026-04-27T15:30:02Z",
        ],
        stdout=out,
        stderr=err,
    )

    assert rc == 0
    assert err.getvalue() == ""
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [row["kind"] for row in rows] == ["tool_event"]
    assert rows[0]["summary"] == "matching tool"
    assert rows[0]["payload"]["tool_name"] == "read_file"


async def test_visibility_tail_websocket_streams_golden_json(teams_root: Path):
    event = _event(
        "tool_event",
        1,
        summary="websocket event",
        payload={"tool_name": "read_file", "status": "success"},
    )

    async with _visibility_ws_server("team-x") as (url, err):
        async with connect(url) as websocket:
            pio.append_event("team-x", "codex-a", event)
            row = await _recv_event(websocket)

    assert err.getvalue() == ""
    assert row == json.loads(event.model_dump_json(by_alias=True, exclude_none=True))


async def test_visibility_tail_websocket_fans_out_to_multiple_clients(
    teams_root: Path,
):
    event = _event(
        "turn_completed",
        2,
        summary="fan-out event",
        payload={"exit_code": 0},
    )

    async with _visibility_ws_server("team-x") as (url, err):
        async with connect(url) as first, connect(url) as second:
            pio.append_event("team-x", "codex-a", event)
            first_row, second_row = await asyncio.gather(
                _recv_event(first),
                _recv_event(second),
            )

    assert err.getvalue() == ""
    assert first_row["event_id"] == event.event_id
    assert second_row["event_id"] == event.event_id


async def test_visibility_tail_websocket_subscription_filters_route_events(
    teams_root: Path,
):
    too_old = _event("tool_event", 2, summary="too old")
    wrong_kind = _event("turn_started", 3, summary="wrong kind")
    gemini_tool = _event("tool_event", 4, agent="gemini-a", summary="gemini tool")
    codex_tool = _event("tool_event", 5, summary="matching codex tool")

    async with _visibility_ws_server("team-x") as (url, err):
        async with connect(url) as codex_client, connect(url) as gemini_client:
            await codex_client.send(
                json.dumps(
                    {
                        "type": "subscribe",
                        "kind": "tool_event",
                        "agent": "codex-a",
                        "since": "2026-04-27T15:30:05Z",
                    }
                )
            )
            assert json.loads(await codex_client.recv()) == {
                "filter": {
                    "agent": "codex-a",
                    "kind": ["tool_event"],
                    "since": "2026-04-27T15:30:05Z",
                },
                "type": "subscribed",
            }

            await gemini_client.send(
                json.dumps({"type": "subscribe", "filter": {"agent": "gemini-a"}})
            )
            assert json.loads(await gemini_client.recv()) == {
                "filter": {"agent": "gemini-a"},
                "type": "subscribed",
            }

            pio.append_event("team-x", "codex-a", too_old)
            pio.append_event("team-x", "codex-a", wrong_kind)
            pio.append_event("team-x", "gemini-a", gemini_tool)
            pio.append_event("team-x", "codex-a", codex_tool)

            codex_row, gemini_row = await asyncio.gather(
                _recv_event(codex_client),
                _recv_event(gemini_client),
            )

    assert err.getvalue() == ""
    assert codex_row["summary"] == "matching codex tool"
    assert codex_row["event_id"] == codex_tool.event_id
    assert gemini_row["summary"] == "gemini tool"
    assert gemini_row["event_id"] == gemini_tool.event_id


def test_visibility_tail_websocket_rejects_non_loopback_bind(teams_root: Path):
    out = io.StringIO()
    err = io.StringIO()

    rc = visibility_tail.main(
        ["--team", "team-x", "--serve", "0.0.0.0:8765"],
        stdout=out,
        stderr=err,
    )

    assert rc == 2
    assert out.getvalue() == ""
    assert "refusing to bind non-loopback host" in err.getvalue()


class _TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_visibility_tail_colors_by_severity_when_tty(teams_root: Path):
    pio.append_event(
        "team-x",
        "codex-a",
        _event("turn_warning", 1, severity="warn", summary="warning event"),
    )
    pio.append_event(
        "team-x",
        "codex-a",
        _event("turn_failed", 2, severity="error", summary="failure event"),
    )

    out = _TtyStringIO()
    err = io.StringIO()
    rc = visibility_tail.main(
        ["--team", "team-x", "--from-start", "--no-follow"],
        stdout=out,
        stderr=err,
    )

    assert rc == 0
    assert err.getvalue() == ""
    rendered = out.getvalue()
    assert "\033[33mWARN\033[0m \033[33mturn_warning\033[0m" in rendered
    assert "\033[31mERROR\033[0m \033[31mturn_failed\033[0m" in rendered
