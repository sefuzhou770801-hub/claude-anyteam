from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from claude_teams import messaging as cs_messaging
from claude_teams import tasks as cs_tasks
from claude_teams import teams as cs_teams
from tools.stress import run_scenario


@pytest.fixture
def isolated_protocol_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    teams_root = tmp_path / "home" / ".claude" / "teams"
    tasks_root = tmp_path / "home" / ".claude" / "tasks"
    monkeypatch.setattr(cs_teams, "TEAMS_DIR", teams_root)
    monkeypatch.setattr(cs_teams, "TASKS_DIR", tasks_root)
    monkeypatch.setattr(cs_tasks, "TASKS_DIR", tasks_root)
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", teams_root)
    return teams_root, tasks_root


def _fake_scorer_modules(*, fail: str | None = None):
    def throughput_score(*, events_dir: Path, scenario: str, run_id: str, out: Path):
        if fail == "throughput":
            raise RuntimeError("boom-throughput")
        doc = {
            "schema_version": 1,
            "scenario": scenario,
            "run_id": run_id,
            "aggregate": {
                "M1_throughput_per_min": {"sum": 1.5, "mean": 0.5, "min": 0.4, "max": 0.6},
                "M5_turn_failed_rate": {"weighted_mean": 0.1},
                "M11b_team_p95_turn_duration_seconds": 84.0,
            },
            "per_agent_files": ["agents/codex-tgt-app.json"],
        }
        (out / "agents").mkdir(parents=True, exist_ok=True)
        (out / "agents" / "codex-tgt-app.json").write_text(json.dumps({"agent": "codex-tgt-app"}) + "\n")
        (out / "scenario.json").write_text(json.dumps(doc) + "\n")
        return doc

    def collab_score(*, events_dir: Path, scenario: str, run_id: str, out: Path):
        if fail == "collab":
            raise RuntimeError("boom-collab")
        doc = {
            "schema_version": 1,
            "scenario": scenario,
            "run_id": run_id,
            "aggregate": {
                "M4_team_cross_peer_ratio": 0.75,
                "M9_team_steer_ack_rate": 0.9,
                "M11a_team_p95_rtt_seconds": 42.0,
                "M13_total_collisions": 0,
            },
            "per_agent_files": ["agents/codex-tgt-app.json"],
            "pair_file": "pairs.json",
        }
        agent_doc = {
            "agent": "codex-tgt-app",
            "metrics": {
                "M11a_peer_dm_rtt_seconds_by_recipient_backend": {
                    "gemini_acp": {"p50": None, "p95": None, "max": 12.0, "samples": 1},
                    "kimi_headless": {"p50": None, "p95": None, "max": None, "samples": 0},
                }
            },
        }
        (out / "agents").mkdir(parents=True, exist_ok=True)
        (out / "agents" / "codex-tgt-app.json").write_text(json.dumps(agent_doc) + "\n")
        (out / "pairs.json").write_text(json.dumps({"pairs": []}) + "\n")
        (out / "scenario.json").write_text(json.dumps(doc) + "\n")
        return doc

    def quality_score(*, events_dir: Path, sandbox: Path, workload_manifest: Path, scenario: str, run_id: str, out: Path):
        if fail == "quality":
            raise RuntimeError("boom-quality")
        doc = {
            "schema_version": 1,
            "scenario": scenario,
            "run_id": run_id,
            "task_verdicts": {"pass": 1, "fail": 0, "deferred": 0, "manual_pending": 0, "unknown_check": 0},
            "task_pass_rate": 1.0,
            "M6_team_total_visibility_degraded": 0,
            "M7_team_total_capability_manifest_calls": 1,
            "M12_team_average_coverage_ratio": 0.8,
            "M12_backends_below_threshold": [],
            "s1_flatten_violations": [],
            "per_task_files": ["tasks/1.json"],
            "per_agent_files": ["agents/codex-tgt-app.json"],
        }
        (out / "tasks").mkdir(parents=True, exist_ok=True)
        (out / "agents").mkdir(parents=True, exist_ok=True)
        (out / "tasks" / "1.json").write_text(json.dumps({"task_id": "1", "verdict": "pass"}) + "\n")
        (out / "agents" / "codex-tgt-app.json").write_text(json.dumps({"agent": "codex-tgt-app", "s1_flatten_violations": []}) + "\n")
        (out / "scenario.json").write_text(json.dumps(doc) + "\n")
        return doc

    return (
        SimpleNamespace(score=Mock(side_effect=throughput_score)),
        SimpleNamespace(score=Mock(side_effect=collab_score)),
        SimpleNamespace(score=Mock(side_effect=quality_score)),
    )


@pytest.fixture
def fake_scorers(monkeypatch: pytest.MonkeyPatch):
    modules = _fake_scorer_modules()
    monkeypatch.setattr(run_scenario, "_load_scorers", lambda: modules)
    return modules


def _workload(name: str = "W1.json") -> Path:
    return Path("tools/stress/workloads") / name


def _invoke(tmp_path: Path, scenario: str, workload: str = "W1.json", run_id: str = "20260427T1530Z", extra: list[str] | None = None) -> tuple[int, Path, Path]:
    out = tmp_path / "runs" / f"{scenario}-{run_id}"
    sandbox = tmp_path / "sandbox"
    argv = [
        "--scenario",
        scenario,
        "--run-id",
        run_id,
        "--workload-manifest",
        str(_workload(workload)),
        "--sandbox",
        str(sandbox),
        "--out",
        str(out),
        "--dry-run",
    ]
    if extra:
        argv.extend(extra)
    return run_scenario.main(argv), out, sandbox


def _scorecard(out: Path) -> dict:
    return json.loads((out / "scorecard.json").read_text())


def test_dry_run_S1(isolated_protocol_roots, fake_scorers, tmp_path: Path) -> None:
    rc, out, _ = _invoke(tmp_path, "S1")

    assert rc == 0
    card = _scorecard(out)
    assert card["scorecards"] == {
        "throughput": "throughput/scenario.json",
        "collab": "collab/scenario.json",
        "quality": "quality/scenario.json",
    }
    assert (out / "events").exists()


def test_dry_run_S5_mixed(isolated_protocol_roots, fake_scorers, tmp_path: Path) -> None:
    rc, out, _ = _invoke(tmp_path, "S5", workload="W9.json")

    assert rc == 0
    agent_doc = json.loads((out / "collab" / "agents" / "codex-tgt-app.json").read_text())
    by_backend = agent_doc["metrics"]["M11a_peer_dm_rtt_seconds_by_recipient_backend"]
    assert "gemini_acp" in by_backend
    headline = _scorecard(out)["headline_metrics"]
    assert headline["M11a_team_p95_rtt_seconds"] == 42.0
    assert headline["M11b_team_p95_turn_duration_seconds"] == 84.0


def test_dry_run_S10a_ablation(isolated_protocol_roots, fake_scorers, tmp_path: Path) -> None:
    rc, out, _ = _invoke(tmp_path, "S10a")

    assert rc == 0
    card = _scorecard(out)
    assert card["env_overrides"] == {"CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS": "1"}
    assert card["ablation_against"] == "S5"


def test_dry_run_S10b_ablation(isolated_protocol_roots, fake_scorers, tmp_path: Path) -> None:
    rc, out, _ = _invoke(tmp_path, "S10b")

    assert rc == 0
    assert _scorecard(out)["env_overrides"] == {
        "CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS": "1",
        "CLAUDE_ANYTEAM_DISABLE_MANIFEST_CACHE": "1",
    }


def test_unknown_scenario_id(isolated_protocol_roots, fake_scorers, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, _, _ = _invoke(tmp_path, "S404")

    assert rc == 1
    assert "unknown scenario" in capsys.readouterr().err


def test_workload_manifest_missing(isolated_protocol_roots, fake_scorers, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "runs" / "missing"
    sandbox = tmp_path / "sandbox"
    rc = run_scenario.main(
        [
            "--scenario",
            "S1",
            "--run-id",
            "MISSING",
            "--workload-manifest",
            str(tmp_path / "missing.json"),
            "--sandbox",
            str(sandbox),
            "--out",
            str(out),
            "--dry-run",
        ]
    )

    assert rc == 1
    assert "workload manifest missing" in capsys.readouterr().err


def test_archive_paths(isolated_protocol_roots, fake_scorers, tmp_path: Path) -> None:
    rc, out, _ = _invoke(tmp_path, "S1")

    assert rc == 0
    assert (out / "events").is_dir()
    assert (out / "tasks").is_dir()
    assert (out / "team-config.json").is_file()
    assert (out / "workload-manifest.json").is_file()
    assert (out / "throughput" / "scenario.json").is_file()
    assert (out / "collab" / "scenario.json").is_file()
    assert (out / "quality" / "scenario.json").is_file()


def test_unified_scorecard_headline_metrics_present(isolated_protocol_roots, fake_scorers, tmp_path: Path) -> None:
    rc, out, _ = _invoke(tmp_path, "S1")

    assert rc == 0
    card = _scorecard(out)
    for key in run_scenario.HEADLINE_KEYS:
        assert key in card["headline_metrics"]
    assert "s1_flatten_violations" in card
    assert "north_star_signals" in card


def test_scorer_failure_caught(isolated_protocol_roots, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    modules = _fake_scorer_modules(fail="collab")
    monkeypatch.setattr(run_scenario, "_load_scorers", lambda: modules)

    rc, out, _ = _invoke(tmp_path, "S1")

    assert rc == 3
    card = _scorecard(out)
    assert any(note.startswith("scorer_failure:") for note in card["notes"])


def test_team_already_exists(isolated_protocol_roots, fake_scorers, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cs_teams.create_team("stress-S1-EXISTS", session_id="preexisting")

    rc, _, _ = _invoke(tmp_path, "S1", run_id="EXISTS")

    assert rc == 2
    assert "team already exists" in capsys.readouterr().err


def test_workload_manifest_w8_chained_delegation() -> None:
    manifest = json.loads(_workload("W8.json").read_text())

    assert manifest["workload_id"] == "W8"
    assert manifest["success_check"]["type"] == "files_modified"
    assert manifest["success_check"]["args"]["min_distinct_authors"] == 2
    assert manifest["success_check"]["args"]["handoff_required"] is True


def test_workload_manifest_w9_capability_aware() -> None:
    manifest = json.loads(_workload("W9.json").read_text())

    assert manifest["workload_id"] == "W9"
    assert manifest["metric_check"]["metric"] == "M7"
    assert "M7" in json.dumps(manifest)


def test_workload_manifest_w10_cross_backend() -> None:
    manifest = json.loads(_workload("W10.json").read_text())

    assert manifest["workload_id"] == "W10"
    assert "RENDEZVOUS_AGREEMENT.txt" in manifest["success_check"]["args"]["must_include"]
    assert manifest["success_check"]["args"]["agreement_marker_required"] is True
