from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import patch

from claude_anyteam import app_server as app_server_mod
from claude_anyteam import codex as codex_mod
from claude_anyteam import prompts as codex_prompts
from claude_anyteam.wrapper_server import build_server


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=check,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Checkpoint E2E")
    _git(repo, "config", "user.email", "checkpoint-e2e@example.test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")


class _NeverCompletesQueue:
    def get(self, timeout=None):
        raise RuntimeError("simulated long-running turn")


class _NeverCompletesAppServer:
    notifications = None

    def __init__(self, *args, **kwargs):
        self.notifications = _NeverCompletesQueue()
        self.interrupts: list[dict] = []

    def start(self):
        pass

    def initialize(self):
        return {}

    def thread_start(self, **kwargs):
        return "thread-timeout"

    def turn_start(self, **kwargs):
        return "turn-timeout"

    def turn_interrupt(self, **kwargs):
        self.interrupts.append(kwargs)

    def close(self):
        pass


def test_checkpoint_commit_persists_before_simulated_app_server_timeout(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "feature.py").write_text("print('partial progress')\n", encoding="utf-8")

    task = type(
        "Task",
        (),
        {
            "id": "45",
            "subject": "long multi-file task",
            "description": "edit files, checkpoint, then keep working",
        },
    )()
    prompt = codex_prompts.v7_task_prompt(
        task,
        agent_name="codex-checkpoint",
        team_name="checkpoint-team",
    )
    assert "checkpoint_commit so progress is not lost on a turn timeout" in prompt

    mcp = build_server(
        [
            "--team",
            "checkpoint-team",
            "--name",
            "codex-checkpoint",
            "--cwd",
            str(repo),
            "--task-id",
            "45",
        ]
    )
    checkpoint = asyncio.run(
        mcp.call_tool(
            "checkpoint_commit",
            {"message": "checkpoint: partial progress before timeout"},
        )
    ).structured_content

    created_clients: list[_NeverCompletesAppServer] = []

    def make_client(*args, **kwargs):
        client = _NeverCompletesAppServer(*args, **kwargs)
        created_clients.append(client)
        return client

    with patch.object(app_server_mod, "AppServerClient", make_client):
        result = codex_mod.app_server_invoke(
            task_prompt=prompt,
            cwd=repo,
            schema=None,
            settings_team="checkpoint-team",
            settings_agent="codex-checkpoint",
            overall_timeout_s=0.01,
            non_progress_warn_s=300,
            task_id="45",
        )

    assert result.exit_code == 124
    assert "did not complete" in (result.error or "")
    assert created_clients[0].interrupts, "timeout path should interrupt the turn"
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == checkpoint["sha"]
    assert (
        _git(repo, "log", "-1", "--pretty=%B").stdout.strip()
        == "checkpoint: partial progress before timeout"
    )
    assert _git(repo, "status", "--porcelain").stdout == ""
