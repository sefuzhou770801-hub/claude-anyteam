"""Wrapper MCP skill-as-tool PoC tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claude_anyteam.wrapper_server import build_server


@pytest.fixture
def isolated_wrapper(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_ANYTEAM_TEAM", "claude-anyteam")
    monkeypatch.setenv("CLAUDE_ANYTEAM_NAME", "skill-tool-test")
    team_root = tmp_path / "teams"
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_teams.TEAMS_DIR", team_root)
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_messaging.TEAMS_DIR", team_root)
    monkeypatch.setattr("claude_anyteam.protocol_io._m.TEAMS_DIR", team_root)
    return build_server()


def _call_tool(mcp, name: str, arguments: dict):
    content = asyncio.run(mcp.call_tool(name, arguments)).structured_content
    if isinstance(content, dict) and set(content) == {"result"}:
        return content["result"]
    return content


def test_list_skills_returns_in_repo_help_diagnose_status(isolated_wrapper):
    skills = _call_tool(isolated_wrapper, "mcp_anyteam_list_skills", {})
    by_name = {entry["name"]: entry for entry in skills}

    assert {"help", "diagnose", "status"} <= set(by_name)
    for skill_name in ("help", "diagnose", "status"):
        assert by_name[skill_name]["source_path"].endswith(f"skills/{skill_name}/SKILL.md")
        assert by_name[skill_name]["description"]


def test_invoke_skill_returns_diagnose_body_and_source_path(isolated_wrapper):
    repo_root = Path(__file__).resolve().parents[1]
    expected_path = (repo_root / "skills" / "diagnose" / "SKILL.md").resolve()
    expected_body = expected_path.read_text(encoding="utf-8")

    result = _call_tool(
        isolated_wrapper,
        "mcp_anyteam_invoke_skill",
        {"skill_name": "diagnose"},
    )

    assert result == {
        "skill_name": "diagnose",
        "body": expected_body,
        "source_path": str(expected_path),
    }


def test_invoke_missing_skill_returns_typed_error(isolated_wrapper):
    result = _call_tool(
        isolated_wrapper,
        "mcp_anyteam_invoke_skill",
        {"skill_name": "nonexistent"},
    )

    assert result == {"error": "skill_not_found", "skill_name": "nonexistent"}
