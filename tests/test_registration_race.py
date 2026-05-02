from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from claude_anyteam.config import Settings
from claude_anyteam import registration as registration_mod
from claude_anyteam import capability_manifest as manifest_mod
from claude_anyteam.cli import main as cli_main
from claude_teams.models import TeammateMember
from claude_teams.teams import add_member, create_team, read_config


def _settings(team: str, name: str, cwd: Path) -> Settings:
    return Settings(
        team_name=team,
        agent_name=name,
        cwd=cwd,
        poll_interval_s=1.5,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
    )


def test_concurrent_register_calls_preserve_both_members(tmp_path: Path, monkeypatch):
    base = tmp_path / "teams"
    team = "race-team"
    cfg_path = base / team / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(
            {
                "name": team,
                "members": [{"name": "team-lead", "agentId": f"team-lead@{team}"}],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(registration_mod, "TEAMS_ROOT", base)

    first_read_started = threading.Event()
    original_read_text = Path.read_text
    slept_once = False
    slept_once_lock = threading.Lock()

    def slow_first_config_read(self: Path, *args, **kwargs):
        nonlocal slept_once
        text = original_read_text(self, *args, **kwargs)
        if self == cfg_path:
            with slept_once_lock:
                if not slept_once:
                    slept_once = True
                    first_read_started.set()
                    # Deterministically widen the stale-read window. Without
                    # the registration lock, the second thread can read the
                    # same config bytes and clobber the first writer.
                    time.sleep(0.1)
        return text

    monkeypatch.setattr(Path, "read_text", slow_first_config_read)

    results: list[dict] = []
    errors: list[Exception] = []

    def do_register(name: str) -> None:
        try:
            results.append(registration_mod.register(_settings(team, name, tmp_path)))
        except Exception as exc:  # pragma: no cover - assertion below reports details
            errors.append(exc)

    t1 = threading.Thread(target=do_register, args=("codex-alpha",))
    t2 = threading.Thread(target=do_register, args=("codex-beta",))
    t1.start()
    assert first_read_started.wait(timeout=1.0), "first register() call never reached config read"
    t2.start()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert not t1.is_alive()
    assert not t2.is_alive()
    assert errors == []
    assert {entry["name"] for entry in results} == {"codex-alpha", "codex-beta"}

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    names = {member["name"] for member in cfg["members"] if isinstance(member, dict)}
    assert names == {"team-lead", "codex-alpha", "codex-beta"}


def _teammate(team: str, name: str, cwd: Path) -> TeammateMember:
    return TeammateMember(
        agent_id=f"{name}@{team}",
        name=name,
        agent_type="teammate",
        model="claude-sonnet-4-20250514",
        prompt="Do stuff",
        color="blue",
        plan_mode_required=False,
        joined_at=int(time.time() * 1000),
        tmux_pane_id="%1",
        cwd=str(cwd),
    )


def test_add_member_and_register_share_config_lock_under_stress(tmp_path: Path, monkeypatch, capsys):
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True)
    team = "mixed-race-team"
    create_team(team, "sess-1", base_dir=claude_dir)
    monkeypatch.setattr(registration_mod, "TEAMS_ROOT", claude_dir / "teams")
    monkeypatch.setattr(Path, "home", lambda: home)

    errors: list[BaseException] = []

    def add_one(i: int) -> None:
        add_member(team, _teammate(team, f"vendored-{i:02d}", tmp_path), base_dir=claude_dir)

    def register_one(i: int) -> None:
        registration_mod.register(_settings(team, f"codex-{i:02d}", tmp_path))

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = []
        for i in range(40):
            futures.append(pool.submit(add_one, i))
            futures.append(pool.submit(register_one, i))
        for future in as_completed(futures):
            try:
                future.result(timeout=5)
            except BaseException as exc:  # pragma: no cover - assertion reports details
                errors.append(exc)

    assert errors == []
    assert (claude_dir / "teams" / team / "config.lock").exists()

    cfg = read_config(team, base_dir=claude_dir)
    names = {member.name for member in cfg.members}
    assert "team-lead" in names
    assert {f"vendored-{i:02d}" for i in range(40)} <= names
    assert {f"codex-{i:02d}" for i in range(40)} <= names
    assert len(names) == 81

    rc = cli_main(["team-roster", "--team", team, "--json"])
    assert rc == 0
    roster = json.loads(capsys.readouterr().out)
    assert len(roster) == 81


def test_concurrent_manifest_writes_use_config_lock_and_leave_valid_json(tmp_path: Path, monkeypatch):
    team_root = tmp_path / "teams" / "manifest-race"
    team_root.mkdir(parents=True)

    original_atomic_write = manifest_mod._atomic_write_json
    active_writers = 0
    max_active_writers = 0
    active_lock = threading.Lock()
    writer_errors: list[str] = []

    def tracked_atomic_write(path: Path, data: dict) -> None:
        nonlocal active_writers, max_active_writers
        with active_lock:
            active_writers += 1
            max_active_writers = max(max_active_writers, active_writers)
            if active_writers > 1:
                writer_errors.append(f"overlap while writing {path}")
        try:
            time.sleep(0.01)
            original_atomic_write(path, data)
        finally:
            with active_lock:
                active_writers -= 1

    monkeypatch.setattr(manifest_mod, "_atomic_write_json", tracked_atomic_write)

    def write_one(i: int) -> Path:
        return manifest_mod.write_manifest(
            team_root,
            f"agent-{i:02d}",
            {
                "schema_version": 1,
                "agent_name": f"agent-{i:02d}",
                "capability_version": str(i),
                "capabilities": {"demo": {"version": "1"}},
            },
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        paths = [future.result(timeout=5) for future in as_completed(pool.submit(write_one, i) for i in range(40))]

    assert writer_errors == []
    assert max_active_writers == 1
    assert (team_root / "config.lock").exists()

    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert payload["agent_name"] == path.stem
