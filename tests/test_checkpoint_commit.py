from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

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
    _git(repo, "config", "user.name", "Checkpoint Test")
    _git(repo, "config", "user.email", "checkpoint@example.test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")


def _checkpoint(repo: Path, message: str) -> dict:
    mcp = build_server(
        [
            "--team",
            "checkpoint-team",
            "--name",
            "codex-checkpoint",
            "--cwd",
            str(repo),
        ]
    )
    result = asyncio.run(mcp.call_tool("checkpoint_commit", {"message": message}))
    return result.structured_content


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    monkeypatch.delenv("CLAUDE_ANYTEAM_TEAM", raising=False)
    monkeypatch.delenv("CLAUDE_ANYTEAM_NAME", raising=False)
    monkeypatch.delenv("CLAUDE_ANYTEAM_CWD", raising=False)


def test_checkpoint_commit_commits_all_changes_and_returns_sha(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "feature.txt").write_text("meaningful progress\n", encoding="utf-8")

    result = _checkpoint(repo, "checkpoint: feature progress")

    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert result["sha"] == head
    assert result["commit"] == head
    assert result["message"] == "checkpoint: feature progress"
    assert result["repo"] == str(repo)
    assert _git(repo, "status", "--porcelain").stdout == ""
    assert (
        _git(repo, "log", "-1", "--pretty=%B").stdout.strip()
        == "checkpoint: feature progress"
    )


def test_checkpoint_commit_fails_when_there_are_no_changes(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)

    with pytest.raises(Exception) as excinfo:
        _checkpoint(repo, "checkpoint: no changes")

    assert "no changes to commit" in str(excinfo.value)


def test_checkpoint_commit_fails_before_staging_unmerged_paths(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)

    _git(repo, "checkout", "-b", "side")
    (repo / "README.md").write_text("side change\n", encoding="utf-8")
    _git(repo, "commit", "-am", "side change")
    _git(repo, "checkout", "main")
    (repo / "README.md").write_text("main change\n", encoding="utf-8")
    _git(repo, "commit", "-am", "main change")
    merge = _git(repo, "merge", "side", check=False)
    assert merge.returncode != 0
    assert _git(repo, "ls-files", "-u").stdout

    with pytest.raises(Exception) as excinfo:
        _checkpoint(repo, "checkpoint: conflicted")

    assert "unresolved merge conflicts" in str(excinfo.value)
