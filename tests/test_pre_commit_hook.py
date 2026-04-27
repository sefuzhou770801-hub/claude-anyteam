"""Tests for the protected-branch pre-commit hook at .githooks/pre-commit.

Validates the three required behaviors per opus-rev-impl's review-gate
discipline:
1. Blocks `git commit` on `proto-rev/impl-2026-04-27` and `main` with a
   helpful error message.
2. Allows commits when ANYTEAM_BYPASS_GATE=1 (emergency override).
3. Allows commits on any feature branch.

The hook ships in `.githooks/pre-commit` and is enabled per-clone via
`git config core.hooksPath .githooks`. These tests run the hook script
directly without the hooksPath config (we invoke the hook from a temp
git repo).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


HOOK_PATH = Path(__file__).resolve().parent.parent / ".githooks" / "pre-commit"


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        env=full_env,
    )


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """A throwaway git repo with the pre-commit hook installed and one
    initial commit on `main`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    # Install hook via core.hooksPath pointing at a copy of the real hook.
    hooks_dir = repo / ".githooks"
    hooks_dir.mkdir()
    shutil.copy2(HOOK_PATH, hooks_dir / "pre-commit")
    (hooks_dir / "pre-commit").chmod(0o755)
    _git(repo, "config", "core.hooksPath", str(hooks_dir))
    # Seed an initial commit so the hook can fire on subsequent commits.
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    # Use bypass for the seed commit (we're on main).
    _git(repo, "commit", "-q", "-m", "seed", env={"ANYTEAM_BYPASS_GATE": "1"})
    return repo


def test_hook_blocks_direct_commit_on_main(tmp_repo: Path):
    (tmp_repo / "x.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_repo, "add", "x.txt")
    result = _git(tmp_repo, "commit", "-m", "should fail")

    assert result.returncode != 0, "commit on main must be blocked"
    assert "direct commits to main are forbidden" in result.stderr
    assert "ANYTEAM_BYPASS_GATE=1" in result.stderr


def test_hook_blocks_direct_commit_on_proto_rev_impl(tmp_repo: Path):
    _git(tmp_repo, "checkout", "-q", "-b", "proto-rev/impl-2026-04-27")
    (tmp_repo / "x.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_repo, "add", "x.txt")
    result = _git(tmp_repo, "commit", "-m", "should fail")

    assert result.returncode != 0
    assert "direct commits to proto-rev/impl-2026-04-27 are forbidden" in result.stderr


def test_hook_allows_commit_with_bypass_env(tmp_repo: Path):
    (tmp_repo / "x.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_repo, "add", "x.txt")
    result = _git(
        tmp_repo,
        "commit",
        "-m",
        "emergency override",
        env={"ANYTEAM_BYPASS_GATE": "1"},
    )

    assert result.returncode == 0, f"bypass should allow: {result.stderr}"
    assert "bypassing review gate" in result.stderr


def test_hook_allows_commits_on_feature_branches(tmp_repo: Path):
    _git(tmp_repo, "checkout", "-q", "-b", "feature/some-work")
    (tmp_repo / "x.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_repo, "add", "x.txt")
    result = _git(tmp_repo, "commit", "-m", "feature work")

    assert result.returncode == 0, f"feature branch commit should pass: {result.stderr}"


def test_hook_allows_commits_on_arbitrary_named_branch(tmp_repo: Path):
    """Sanity: non-protected branch names like impl-* or worker-* aren't
    accidentally swept up by the case match."""
    for branch in ("impl-r99", "worker/codex-x", "fix/typo"):
        _git(tmp_repo, "checkout", "-q", "-b", branch)
        (tmp_repo / f"{branch.replace('/','_')}.txt").write_text("x\n", encoding="utf-8")
        _git(tmp_repo, "add", "-A")
        result = _git(tmp_repo, "commit", "-m", f"work on {branch}")
        assert result.returncode == 0, f"{branch} should not be blocked: {result.stderr}"
