from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]

from claude_anyteam import protocol_io as pio
from claude_anyteam.messages import VisibilityEvent

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from tools.stress import score_throughput

BASE = datetime(2026, 4, 27, 15, 30, tzinfo=timezone.utc)


@pytest.fixture
def teams_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "home" / ".claude" / "teams"
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", base)
    return base


def _ts(seconds: float) -> str:
    dt = BASE + timedelta(seconds=seconds)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _event(
    kind: str,
    seq: int,
    *,
    seconds: float,
    team: str = "team-x",
    agent: str = "agent-a",
    backend: str = "codex_app_server",
    payload: dict | None = None,
) -> VisibilityEvent:
    return VisibilityEvent.model_validate(
        {
            "kind": kind,
            "event_id": f"{agent}:turn:{seq:06d}",
            "timestamp": _ts(seconds),
            "team": team,
            "agent": agent,
            "backend": backend,
            "task_id": "t1",
            "turn_id": "turn-1",
            "seq": seq,
            "severity": "warn" if kind == "turn_warning" else "info",
            "summary": f"{kind} {seq}",
            "payload": payload or {},
        }
    )


def _append(kind: str, seq: int, *, seconds: float, team: str = "team-x", agent: str = "agent-a", payload: dict | None = None) -> None:
    pio.append_event(team, agent, _event(kind, seq, seconds=seconds, team=team, agent=agent, payload=payload))


def _run_team(team: str, out: Path) -> int:
    return score_throughput.main(
        [
            "--team",
            team,
            "--scenario",
            "S1",
            "--run-id",
            "20260427T1530Z",
            "--out",
            str(out),
        ]
    )


def _agent_score(out: Path, agent: str = "agent-a") -> dict:
    return json.loads((out / "agents" / f"{agent}.json").read_text())


def test_empty_events_dir(tmp_path: Path) -> None:
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    out = tmp_path / "out"

    rc = score_throughput.main(
        [
            "--events-dir",
            str(events_dir),
            "--scenario",
            "S1",
            "--run-id",
            "20260427T1530Z",
            "--out",
            str(out),
        ]
    )

    assert rc == 1
    assert not out.exists()


def test_per_agent_basic(teams_root: Path, tmp_path: Path) -> None:
    _append("turn_completed", 1, seconds=0)
    _append("turn_completed", 2, seconds=60)
    _append("turn_warning", 3, seconds=90)
    _append("turn_completed", 4, seconds=120)
    _append("turn_completed", 5, seconds=180)
    _append("turn_completed", 6, seconds=240)

    out = tmp_path / "score"
    assert _run_team("team-x", out) == 0

    score = _agent_score(out)
    metrics = score["metrics"]
    assert metrics["M1_throughput_per_min"] == pytest.approx(1.25)
    assert metrics["M5_turn_failed_rate"] == 0.0
    assert metrics["M5_turn_completed_count"] == 5
    assert metrics["M10_turn_warning_count"] == 1
    assert score["backend"] == "codex_app_server"


def test_raw_backend_type_preserved_verbatim(teams_root: Path, tmp_path: Path) -> None:
    _append(
        "tool_event",
        1,
        seconds=0,
        payload={
            "category": "host_tool",
            "tool_name": "commandExecution",
            "raw_backend_type": "commandExecution",
        },
    )
    _append(
        "tool_event",
        2,
        seconds=10,
        payload={
            "category": "host_tool",
            "tool_name": "webSearch",
            "raw_backend_type": "webSearch",
        },
    )

    out = tmp_path / "score"
    assert _run_team("team-x", out) == 0

    by_raw = _agent_score(out)["metrics"]["M2_tool_event_count"]["by_raw_backend_type"]
    assert by_raw["commandExecution"] == 1
    assert by_raw["webSearch"] == 1
    assert "tool_call" not in by_raw


def test_idle_time_p95(teams_root: Path, tmp_path: Path) -> None:
    events = [
        ("turn_started", 1, 0),
        ("turn_completed", 2, 10),
        ("turn_started", 3, 11),
        ("turn_completed", 4, 20),
        ("turn_started", 5, 23),
        ("turn_completed", 6, 30),
        ("turn_started", 7, 35),
        ("turn_completed", 8, 40),
        ("turn_started", 9, 47),
        ("turn_completed", 10, 50),
        ("turn_started", 11, 61),
        ("turn_completed", 12, 70),
        ("turn_started", 13, 83),
        ("turn_completed", 14, 90),
    ]
    for kind, seq, seconds in events:
        _append(kind, seq, seconds=seconds)

    out = tmp_path / "score"
    assert _run_team("team-x", out) == 0

    idle = _agent_score(out)["metrics"]["M8_idle_time_seconds"]
    samples = [1, 3, 5, 7, 11, 13]
    assert idle["samples"] == 6
    assert idle["mean"] == pytest.approx(statistics.mean(samples))
    assert idle["median"] == pytest.approx(statistics.median(samples))
    assert idle["p95"] == pytest.approx(statistics.quantiles(samples, n=20)[18])


def test_turn_started_without_completion(teams_root: Path, tmp_path: Path) -> None:
    _append("turn_started", 1, seconds=0)
    _append("turn_completed", 2, seconds=10)
    _append("turn_started", 3, seconds=20)  # pending/in-flight, excluded from M5 denominator

    out = tmp_path / "score"
    assert _run_team("team-x", out) == 0

    metrics = _agent_score(out)["metrics"]
    assert metrics["M5_turn_completed_count"] == 1
    assert metrics["M5_turn_failed_count"] == 0
    assert metrics["M5_turn_failed_rate"] == 0.0


def test_clock_regress_tolerated(teams_root: Path, tmp_path: Path) -> None:
    _append("turn_completed", 1, seconds=100)
    _append("turn_warning", 2, seconds=0)
    _append("turn_completed", 3, seconds=50)

    out = tmp_path / "score"
    assert _run_team("team-x", out) == 0

    score = _agent_score(out)
    assert score["wall_clock_seconds"] == 100.0
    assert "clock_regress_observed" in score["notes"]


def test_partial_events_only_flag(teams_root: Path, tmp_path: Path) -> None:
    _append("turn_started", 1, seconds=0)
    _append(
        "turn_completed",
        2,
        seconds=10,
        payload={
            "partial_events_available": False,
            "tool_call_events": 3,
        },
    )

    out = tmp_path / "score"
    assert _run_team("team-x", out) == 0

    m2 = _agent_score(out)["metrics"]["M2_tool_event_count"]
    assert m2["total"] == 3
    assert m2["by_category"]["_unknown_category"] == 3
    assert m2["notes"] == ["partial_events_only"]


def test_malformed_jsonl_line_is_skipped(tmp_path: Path) -> None:
    run_dir = tmp_path / "archived-run"
    events_dir = run_dir / "events"
    events_dir.mkdir(parents=True)
    good = _event(
        "turn_completed",
        1,
        seconds=10,
        team=run_dir.name,
        agent="agent-a",
    ).model_dump_json(by_alias=True)
    (events_dir / "agent-a.jsonl").write_text("{not valid json}\n" + good + "\n", encoding="utf-8")
    out = tmp_path / "score"

    rc = score_throughput.main(
        [
            "--events-dir",
            str(events_dir),
            "--scenario",
            "S1",
            "--run-id",
            "20260427T1530Z",
            "--out",
            str(out),
        ]
    )

    assert rc == 0
    metrics = _agent_score(out)["metrics"]
    assert metrics["M5_turn_completed_count"] == 1
    assert metrics["M5_turn_failed_count"] == 0
