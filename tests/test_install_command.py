from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from claude_anyteam import cli as cli_mod
from claude_anyteam import installer as installer_mod


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _stub_prereq_found(monkeypatch: pytest.MonkeyPatch, binary: str = "tmux") -> None:
    """Replace the real terminal-multiplexer probe with a "found" stub.

    Keeps the existing-test pattern of "stub the resolution, assert the rest".
    """

    def _stub() -> installer_mod.PrereqCheck:
        return installer_mod.PrereqCheck(
            found=True,
            binary=binary,
            path=Path(f"/usr/bin/{binary}"),
            platform="linux",
        )

    monkeypatch.setattr(installer_mod, "_check_terminal_multiplexer", _stub)


def _stub_prereq_missing(monkeypatch: pytest.MonkeyPatch, platform: str = "linux") -> None:
    def _stub() -> installer_mod.PrereqCheck:
        return installer_mod.PrereqCheck(found=False, binary=None, path=None, platform=platform)

    monkeypatch.setattr(installer_mod, "_check_terminal_multiplexer", _stub)


def _fresh_paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Standard path fixtures used across install/uninstall tests."""
    settings_path = tmp_path / "home" / ".claude" / "settings.json"
    claude_json_path = tmp_path / "home" / ".claude.json"
    state_path = (
        tmp_path / "home" / ".claude" / "plugins" / "data"
        / installer_mod.PLUGIN_DATA_DIR_NAME / installer_mod.STATE_FILE_NAME
    )
    bin_dir = tmp_path / "venv" / "bin"
    return settings_path, claude_json_path, state_path, bin_dir


def _install_argv(
    settings_path: Path,
    claude_json_path: Path,
    state_path: Path,
    *extra: str,
) -> list[str]:
    return [
        "install",
        "--settings-path",
        str(settings_path),
        "--claude-json-path",
        str(claude_json_path),
        "--state-path",
        str(state_path),
        *extra,
    ]


def _uninstall_argv(
    settings_path: Path,
    claude_json_path: Path,
    state_path: Path,
) -> list[str]:
    return [
        "uninstall",
        "--settings-path",
        str(settings_path),
        "--claude-json-path",
        str(claude_json_path),
        "--state-path",
        str(state_path),
    ]


# ---------------------------------------------------------------------------
# Existing coverage, updated to stub the prereq check.
# ---------------------------------------------------------------------------

def test_install_creates_settings_and_sets_required_env_keys(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    shim_binary = _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0

    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload == {
        "env": {
            installer_mod.TEAMMATE_COMMAND_KEY: str(shim_binary.resolve()),
            installer_mod.TEAMMATE_BINARY_KEY: str(codex_binary.resolve()),
        }
    }

    stdout = capsys.readouterr().out
    assert f"Updated {settings_path.resolve()}" in stdout
    assert f"Set env.{installer_mod.TEAMMATE_COMMAND_KEY}={shim_binary.resolve()}" in stdout
    assert f"Set env.{installer_mod.TEAMMATE_BINARY_KEY}={codex_binary.resolve()}" in stdout
    assert "Restart Claude Code for the changes to take effect." in stdout


def test_install_preserves_other_settings_and_env_entries(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, _ = _fresh_paths(tmp_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "env": {
                    "KEEP_ME": "yes",
                    installer_mod.TEAMMATE_COMMAND_KEY: "/old/shim",
                },
            }
        ),
        encoding="utf-8",
    )

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(
        installer_mod.shutil,
        "which",
        lambda name: {
            installer_mod.SHIM_BASENAME: "/opt/tools/claude-anyteam-spawn-shim",
            installer_mod.BINARY_BASENAME: "/opt/tools/claude-anyteam",
        }.get(name),
    )

    result = installer_mod.install(
        settings_path=settings_path,
        claude_json_path=claude_json_path,
        state_path=state_path,
    )

    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload["theme"] == "dark"
    assert payload["env"] == {
        "KEEP_ME": "yes",
        installer_mod.TEAMMATE_COMMAND_KEY: "/opt/tools/claude-anyteam-spawn-shim",
        installer_mod.TEAMMATE_BINARY_KEY: "/opt/tools/claude-anyteam",
    }
    assert result.paths.settings_path == settings_path.resolve()


def test_uninstall_removes_only_target_env_keys(
    tmp_path: Path,
    capsys,
):
    settings_path, claude_json_path, state_path, _ = _fresh_paths(tmp_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "env": {
                    "KEEP_ME": "yes",
                    installer_mod.TEAMMATE_COMMAND_KEY: "/opt/tools/claude-anyteam-spawn-shim",
                    installer_mod.TEAMMATE_BINARY_KEY: "/opt/tools/claude-anyteam",
                },
            }
        ),
        encoding="utf-8",
    )

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload == {
        "theme": "dark",
        "env": {
            "KEEP_ME": "yes",
        },
    }

    stdout = capsys.readouterr().out
    assert f"Updated {settings_path.resolve()}" in stdout
    assert "Removed env.CLAUDE_CODE_TEAMMATE_COMMAND, env.CLAUDE_ANYTEAM_BINARY" in stdout
    assert "Restart Claude Code for the changes to take effect." in stdout


def test_main_install_uses_subcommand_path(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0
    assert settings_path.exists()


# ---------------------------------------------------------------------------
# Prereq gate
# ---------------------------------------------------------------------------

def test_install_fails_when_multiplexer_missing(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_missing(monkeypatch, platform="linux")
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(bin_dir / "claude-anyteam")])

    exit_code = cli_mod.main(_install_argv(settings_path, claude_json_path, state_path))
    assert exit_code != 0

    captured = capsys.readouterr()
    assert "requires a terminal multiplexer" in captured.err
    assert "sudo apt install tmux" in captured.err
    assert not settings_path.exists()
    assert not claude_json_path.exists()
    assert not state_path.exists()


def test_install_fails_lists_psmux_on_windows(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_missing(monkeypatch, platform="windows")
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(bin_dir / "claude-anyteam")])

    exit_code = cli_mod.main(_install_argv(settings_path, claude_json_path, state_path))
    assert exit_code != 0
    assert "winget install psmux" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# teammateMode install branches
# ---------------------------------------------------------------------------

def test_install_writes_teammate_mode_when_absent(tmp_path: Path, monkeypatch, capsys):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json == {installer_mod.TEAMMATE_MODE_KEY: "tmux"}

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state == {
        "schema_version": installer_mod.STATE_SCHEMA_VERSION,
        "teammateMode_original": None,
        "teammateMode_set_by_anyteam": True,
    }

    stdout = capsys.readouterr().out
    assert f"Set {installer_mod.TEAMMATE_MODE_KEY}=\"tmux\" in {claude_json_path.resolve()}" in stdout


def test_install_is_noop_when_teammate_mode_already_tmux(tmp_path: Path, monkeypatch, capsys):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "tmux"}) + "\n",
        encoding="utf-8",
    )

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json == {"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "tmux"}

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state == {
        "schema_version": installer_mod.STATE_SCHEMA_VERSION,
        "teammateMode_original": "tmux",
        "teammateMode_set_by_anyteam": False,
    }

    stdout = capsys.readouterr().out
    assert f"{installer_mod.TEAMMATE_MODE_KEY} already \"tmux\"" in stdout


def test_install_prompts_and_overwrites_auto_when_accepted(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "auto"}) + "\n",
        encoding="utf-8",
    )

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)

    seen: list[str] = []

    def prompt(current: str) -> bool:
        seen.append(current)
        return True

    result = installer_mod.install(
        settings_path=settings_path,
        claude_json_path=claude_json_path,
        state_path=state_path,
        argv0=str(bin_dir / "claude-anyteam"),
        prompt_fn=prompt,
    )
    assert seen == ["auto"]
    assert result.teammate_mode is not None
    assert result.teammate_mode.previous_value == "auto"

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json == {"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "tmux"}

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state == {
        "schema_version": installer_mod.STATE_SCHEMA_VERSION,
        "teammateMode_original": "auto",
        "teammateMode_set_by_anyteam": True,
    }


def test_install_prompts_on_in_process_and_overwrites_when_accepted(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({installer_mod.TEAMMATE_MODE_KEY: "in-process"}) + "\n",
        encoding="utf-8",
    )

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)

    installer_mod.install(
        settings_path=settings_path,
        claude_json_path=claude_json_path,
        state_path=state_path,
        argv0=str(bin_dir / "claude-anyteam"),
        prompt_fn=lambda _current: True,
    )

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json[installer_mod.TEAMMATE_MODE_KEY] == "tmux"

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["teammateMode_original"] == "in-process"
    assert state["teammateMode_set_by_anyteam"] is True


def test_install_rolls_back_when_prompt_declined(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "auto"}) + "\n",
        encoding="utf-8",
    )

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)

    with pytest.raises(installer_mod.InstallError) as excinfo:
        installer_mod.install(
            settings_path=settings_path,
            claude_json_path=claude_json_path,
            state_path=state_path,
            argv0=str(codex_binary),
            prompt_fn=lambda _current: False,
        )

    assert getattr(excinfo.value, "cli_exit_code", None) == 3

    # Rollback: settings.json must be absent (we just created it and then rolled back).
    assert not settings_path.exists()
    # claude.json untouched — still has 'auto'.
    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json[installer_mod.TEAMMATE_MODE_KEY] == "auto"
    # State file never written.
    assert not state_path.exists()


def test_install_rolls_back_preserving_preexisting_env_block(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    pre_settings = {
        "theme": "dark",
        "env": {"KEEP_ME": "yes", installer_mod.TEAMMATE_COMMAND_KEY: "/old/shim"},
    }
    settings_path.write_text(json.dumps(pre_settings) + "\n", encoding="utf-8")

    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({installer_mod.TEAMMATE_MODE_KEY: "in-process"}) + "\n",
        encoding="utf-8",
    )

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)

    with pytest.raises(installer_mod.InstallError):
        installer_mod.install(
            settings_path=settings_path,
            claude_json_path=claude_json_path,
            state_path=state_path,
            argv0=str(codex_binary),
            prompt_fn=lambda _current: False,
        )

    restored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert restored == pre_settings, "rollback must restore the original env block byte-for-byte"
    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json[installer_mod.TEAMMATE_MODE_KEY] == "in-process"
    assert not state_path.exists()


def test_install_assume_yes_bypasses_prompt(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({installer_mod.TEAMMATE_MODE_KEY: "auto"}) + "\n",
        encoding="utf-8",
    )

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(
        _install_argv(settings_path, claude_json_path, state_path, "--assume-yes")
    ) == 0

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json[installer_mod.TEAMMATE_MODE_KEY] == "tmux"


# ---------------------------------------------------------------------------
# teammateMode uninstall branches
# ---------------------------------------------------------------------------

def _write_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")


def test_uninstall_restores_original_auto(tmp_path: Path):
    settings_path, claude_json_path, state_path, _ = _fresh_paths(tmp_path)
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "tmux"}) + "\n",
        encoding="utf-8",
    )
    _write_state(
        state_path,
        {
            "schema_version": installer_mod.STATE_SCHEMA_VERSION,
            "teammateMode_original": "auto",
            "teammateMode_set_by_anyteam": True,
        },
    )

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json == {"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "auto"}
    assert not state_path.exists()


def test_uninstall_removes_key_when_we_added_it(tmp_path: Path):
    settings_path, claude_json_path, state_path, _ = _fresh_paths(tmp_path)
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "tmux"}) + "\n",
        encoding="utf-8",
    )
    _write_state(
        state_path,
        {
            "schema_version": installer_mod.STATE_SCHEMA_VERSION,
            "teammateMode_original": None,
            "teammateMode_set_by_anyteam": True,
        },
    )

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert installer_mod.TEAMMATE_MODE_KEY not in claude_json
    assert claude_json == {"theme": "dark"}
    assert not state_path.exists()


def test_uninstall_leaves_teammate_mode_alone_when_not_managed(tmp_path: Path):
    settings_path, claude_json_path, state_path, _ = _fresh_paths(tmp_path)
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({installer_mod.TEAMMATE_MODE_KEY: "tmux"}) + "\n",
        encoding="utf-8",
    )
    _write_state(
        state_path,
        {
            "schema_version": installer_mod.STATE_SCHEMA_VERSION,
            "teammateMode_original": "tmux",
            "teammateMode_set_by_anyteam": False,
        },
    )

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json == {installer_mod.TEAMMATE_MODE_KEY: "tmux"}
    assert not state_path.exists()


def test_uninstall_graceful_when_state_missing(tmp_path: Path, capsys):
    settings_path, claude_json_path, state_path, _ = _fresh_paths(tmp_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "env": {
                    "KEEP_ME": "yes",
                    installer_mod.TEAMMATE_COMMAND_KEY: "/opt/tools/claude-anyteam-spawn-shim",
                    installer_mod.TEAMMATE_BINARY_KEY: "/opt/tools/claude-anyteam",
                }
            }
        ),
        encoding="utf-8",
    )
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({installer_mod.TEAMMATE_MODE_KEY: "tmux"}) + "\n",
        encoding="utf-8",
    )
    # No state file — mirrors an existing install that predates this feature.

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    # claude.json untouched (we don't know whether we "own" the value).
    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json == {installer_mod.TEAMMATE_MODE_KEY: "tmux"}

    # settings.json env block still unwound correctly.
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings == {"env": {"KEEP_ME": "yes"}}

    stdout = capsys.readouterr().out
    assert "Restart Claude Code for the changes to take effect." in stdout


# ---------------------------------------------------------------------------
# Round-trip smoke: install → inspect state → uninstall → everything reverted.
# ---------------------------------------------------------------------------

def test_install_then_uninstall_round_trip_preserves_user_state(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    pre_settings = {"theme": "dark", "env": {"KEEP_ME": "yes"}}
    pre_claude_json = {
        "projects": {"/home/user/foo": {"allowedTools": []}},
        installer_mod.TEAMMATE_MODE_KEY: "auto",
    }
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(pre_settings) + "\n", encoding="utf-8")
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(json.dumps(pre_claude_json) + "\n", encoding="utf-8")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(
        _install_argv(settings_path, claude_json_path, state_path, "--assume-yes")
    ) == 0

    # State: we overwrote auto.
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["teammateMode_original"] == "auto"
    assert state["teammateMode_set_by_anyteam"] is True

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    # Everything back to pre-install. settings.json: env block stripped but
    # other keys intact.
    post_settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert post_settings == pre_settings
    post_claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert post_claude_json == pre_claude_json
    assert not state_path.exists()
