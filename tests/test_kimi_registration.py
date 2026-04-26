from __future__ import annotations

import json
from pathlib import Path

from claude_anyteam import registration as registration_mod
from claude_anyteam.backends.kimi.config import KimiSettings
from claude_anyteam.registration import BackendMetadata, deregister, register


KIMI_METADATA = BackendMetadata(
    model="kimi-cli",
    prompt=(
        "Kimi teammate adapter. Protocol I/O is handled by the adapter; "
        "coding work is delegated to Kimi CLI headless mode. No Claude LLM is involved."
    ),
)


def _settings(team: str, name: str, cwd: Path) -> KimiSettings:
    return KimiSettings(
        team_name=team,
        agent_name=name,
        cwd=cwd,
        poll_interval_s=1.0,
        color="cyan",
        plan_mode_required=False,
    )


def _seed_team(root: Path, team: str) -> Path:
    cfg = root / team / "config.json"
    cfg.parent.mkdir(parents=True)
    (cfg.parent / "inboxes").mkdir()
    cfg.write_text(json.dumps({"name": team, "members": []}), encoding="utf-8")
    return cfg


def _member(cfg_path: Path, name: str) -> dict:
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    return next(member for member in cfg["members"] if member.get("name") == name)


def test_kimi_registration_metadata(tmp_path: Path, monkeypatch):
    root = tmp_path / "teams"
    team = "t"
    name = "kimi-a"
    monkeypatch.setattr(registration_mod, "TEAMS_ROOT", root)
    cfg_path = _seed_team(root, team)

    entry = register(_settings(team, name, tmp_path), KIMI_METADATA)
    persisted = _member(cfg_path, name)

    assert entry["model"] == "kimi-cli"
    assert persisted["model"] == "kimi-cli"
    assert entry["agentType"] == "claude-anyteam"
    assert persisted["agentType"] == "claude-anyteam"
    assert "Kimi teammate adapter" in entry["prompt"]
    assert "Kimi teammate adapter" in persisted["prompt"]
    assert entry["backendType"] == "in-process"
    assert persisted["backendType"] == "in-process"
    assert persisted["agentId"] == f"{name}@{team}"

    inbox = root / team / "inboxes" / f"{name}.json"
    assert inbox.exists()
    assert json.loads(inbox.read_text(encoding="utf-8")) == []


def test_kimi_deregister_cleans_member_and_inbox(tmp_path: Path, monkeypatch):
    root = tmp_path / "teams"
    team = "t"
    name = "kimi-a"
    monkeypatch.setattr(registration_mod, "TEAMS_ROOT", root)
    cfg_path = _seed_team(root, team)
    settings = _settings(team, name, tmp_path)

    register(settings, KIMI_METADATA)
    inbox = root / team / "inboxes" / f"{name}.json"
    assert _member(cfg_path, name)["model"] == "kimi-cli"
    assert inbox.exists()

    assert deregister(settings) is True
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert all(member.get("name") != name for member in cfg["members"])
    assert not inbox.exists()

    assert deregister(settings) is False
