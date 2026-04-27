from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from claude_anyteam.messages import VisibilityEvent
from tools.stress import score_quality as sq


def _task(check: dict, *, task_id: str = "1", workload_id: str = "W1") -> dict:
    return {
        "task_id": task_id,
        "workload_id": workload_id,
        "owner_expected": "codex-tgt-app",
        "success_check": check,
    }


def _make_pytest_repo(sandbox: Path, test_body: str) -> Path:
    repo = sandbox / "repo"
    tests = repo / "tests"
    tests.mkdir(parents=True)
    (tests / "test_sample.py").write_text(test_body, encoding="utf-8")
    return repo


def _event(
    kind: str,
    seq: int,
    *,
    backend: str = "codex_app_server",
    agent: str = "codex-tgt-app",
    task_id: str | None = "1",
    payload: dict | None = None,
) -> VisibilityEvent:
    return VisibilityEvent.model_validate(
        {
            "kind": kind,
            "event_id": f"{agent}:turn:{seq:06d}",
            "team": "proto-rev",
            "agent": agent,
            "backend": backend,
            "task_id": task_id,
            "turn_id": "turn-1",
            "seq": seq,
            "severity": "info",
            "summary": f"event {seq}",
            "payload": payload or {},
        }
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", ".")
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Test User",
            "commit",
            "-m",
            message,
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_pytest_passes_verdict_pass(tmp_path: Path):
    sandbox = tmp_path / "sandbox"
    _make_pytest_repo(sandbox, "def test_ok():\n    assert True\n")

    result = sq.score_task(
        _task({"type": "pytest_passes", "args": ["tests"], "cwd": "<sandbox>/repo"}),
        sandbox=sandbox,
    )

    assert result["verdict"] == "pass"
    assert result["evidence"]["exit_code"] == 0


def test_pytest_passes_verdict_fail(tmp_path: Path):
    sandbox = tmp_path / "sandbox"
    _make_pytest_repo(sandbox, "def test_bad():\n    assert False\n")

    result = sq.score_task(
        _task({"type": "pytest_passes", "args": ["tests"], "cwd": "<sandbox>/repo"}),
        sandbox=sandbox,
    )

    assert result["verdict"] == "fail"
    assert result["evidence"]["exit_code"] != 0


def test_pytest_passes_timeout(tmp_path: Path):
    sandbox = tmp_path / "sandbox"
    _make_pytest_repo(
        sandbox,
        "import time\n\ndef test_sleep():\n    time.sleep(5)\n",
    )

    result = sq.score_task(
        _task(
            {
                "type": "pytest_passes",
                "args": ["tests"],
                "cwd": "<sandbox>/repo",
                "timeout_s": 0.25,
            }
        ),
        sandbox=sandbox,
        pytest_timeout_s=0.25,
    )

    assert result["verdict"] == "fail"
    assert "timeout" in result["notes"]
    assert result["evidence"]["duration_seconds"] == pytest.approx(0.25, abs=0.5)


def test_files_modified_check(tmp_path: Path):
    sandbox = tmp_path / "sandbox"
    repo = sandbox / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("print('one')\n", encoding="utf-8")
    (repo / "src" / "bar.py").write_text("print('bar')\n", encoding="utf-8")
    _commit_all(repo, "initial")
    (repo / "src" / "foo.py").write_text("print('two')\n", encoding="utf-8")
    _commit_all(repo, "modify foo")

    result = sq.score_task(
        _task(
            {
                "type": "files_modified",
                "args": {"max_files": 2, "must_include": ["src/foo.py"]},
            }
        ),
        sandbox=sandbox,
    )

    assert result["verdict"] == "pass"
    assert result["evidence"]["files"] == ["src/foo.py"]


def test_mcp_tool_listed_via_probe(monkeypatch):
    monkeypatch.setattr(sq, "_list_mcp_tools_for_probe", lambda: ["send_message", "mcp_anyteam_grep"])

    result = sq.score_task(
        _task({"type": "mcp_tool_listed", "args": {"tool_name": "mcp_anyteam_grep"}}),
        sandbox=Path("/tmp/unused"),
    )

    assert result["verdict"] == "pass"
    assert "mcp_anyteam_grep" in result["evidence"]["listed_tools"]


def test_unknown_check_type_does_not_crash(tmp_path: Path):
    result = sq.score_task(_task({"type": "fake_check"}), sandbox=tmp_path)

    assert result["verdict"] == "unknown_check"
    assert "unknown_check_type:fake_check" in result["notes"]


def test_m6_count_with_surfaces():
    profile = sq.score_agent_events(
        agent="codex-tgt-app",
        events=[
            _event("visibility_degraded", 1, payload={"surface": "peer_dm"}),
            _event("visibility_degraded", 2, payload={"surface": "host_tool_stream"}),
        ],
        scenario="S1",
        run_id="run-1",
    )

    assert profile["metrics"]["M6_visibility_degraded_count"] == 2
    assert profile["metrics"]["M6_by_surface"]["peer_dm"] == 1
    assert profile["metrics"]["M6_by_surface"]["host_tool_stream"] == 1


def test_m7_capability_manifest_calls():
    events = [
        _event(
            "tool_event",
            seq,
            payload={"tool_name": sq.CAPABILITY_MANIFEST_TOOL, "category": "mcp_tool"},
        )
        for seq in range(1, 4)
    ]

    profile = sq.score_agent_events(agent="codex-tgt-app", events=events, scenario="S1", run_id="run-1")

    assert profile["metrics"]["M7_capability_manifest_calls"] == 3


def test_m12_full_coverage():
    expected = sq.EXPECTED_KINDS_PER_BACKEND["codex_app_server"]
    events = [_event(kind, idx + 1, payload={"category": "host_tool", "tool_name": "commandExecution"} if kind == "tool_event" else {}) for idx, kind in enumerate(expected)]

    profile = sq.score_agent_events(agent="codex-tgt-app", events=events, scenario="S1", run_id="run-1")
    m12 = profile["metrics"]["M12_envelope_shape_coverage"]

    assert m12["coverage_ratio"] == 1.0
    assert m12["missing_kinds"] == []


def test_m12_partial_coverage():
    events = [
        _event("turn_started", 1),
        _event("tool_event", 2, payload={"category": "host_tool", "tool_name": "commandExecution"}),
        _event("turn_completed", 3),
        _event("artifact_event", 4),
    ]

    profile = sq.score_agent_events(agent="codex-tgt-app", events=events, scenario="S1", run_id="run-1")
    m12 = profile["metrics"]["M12_envelope_shape_coverage"]

    assert m12["coverage_ratio"] == 0.571
    assert m12["missing_kinds"] == ["steer_ack", "turn_failed", "turn_progress"]


def test_m12_partial_events_flag_pardons_digest_backends():
    events = [
        _event("turn_started", 1, backend="gemini_headless", agent="gemini-tgt"),
        _event(
            "turn_completed",
            2,
            backend="gemini_headless",
            agent="gemini-tgt",
            payload={"partial_events_available": False},
        ),
    ]

    profile = sq.score_agent_events(agent="gemini-tgt", events=events, scenario="S1", run_id="run-1")
    m12 = profile["metrics"]["M12_envelope_shape_coverage"]

    assert m12["partial_events_only"] is True
    assert m12["missing_kinds"] == ["turn_failed"]
    assert m12["coverage_ratio"] == 0.667


def test_m12_unknown_backend():
    events = [_event("turn_started", 1, backend="new_backend")]

    profile = sq.score_agent_events(agent="future-tgt", events=events, scenario="S1", run_id="run-1")
    m12 = profile["metrics"]["M12_envelope_shape_coverage"]

    assert m12["coverage_ratio"] is None
    assert "unknown_backend:new_backend" in profile["notes"]


def test_s1_flatten_violation_detected():
    events = [
        _event(
            "tool_event",
            1,
            backend="codex_app_server",
            payload={"category": "host_tool", "tool_name": "tool_call"},
        )
    ]

    profile = sq.score_agent_events(agent="codex-tgt-app", events=events, scenario="S1", run_id="run-1")

    assert profile["s1_flatten_violations"] == [
        {
            "event_id": "codex-tgt-app:turn:000001",
            "raw_tool_name": "tool_call",
            "backend": "codex_app_server",
            "kind": "tool_event",
        }
    ]


def test_s1_flatten_no_false_positive_for_gemini():
    events = [
        _event(
            "tool_event",
            1,
            backend="gemini_acp",
            agent="gemini-tgt",
            payload={"category": "host_tool", "tool_name": "tool_call"},
        )
    ]

    profile = sq.score_agent_events(agent="gemini-tgt", events=events, scenario="S1", run_id="run-1")

    assert profile["s1_flatten_violations"] == []
    assert profile["metrics"]["s1_generic_tool_call_count"] == 1


def test_m7_zero_in_w7_flagged(tmp_path: Path):
    result = sq.score_task(
        _task({"type": "manual", "args": {"description": "hand review"}}, workload_id="W7"),
        sandbox=tmp_path,
        task_m7=0,
    )

    assert result["verdict"] == "manual_pending"
    assert "m7_zero_in_collab_workload" in result["notes"]
