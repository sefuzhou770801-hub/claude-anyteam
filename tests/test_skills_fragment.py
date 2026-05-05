"""Unit tests for ``claude_anyteam.skills_fragment`` (v0.8.4).

These cover the C-side compose path in isolation. The integration with A's
MCP tools and the live cross-backend flow is locked by
``tests/test_cross_backend_skills_integration.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_anyteam import loop as codex_loop
from claude_anyteam.codex import CodexResult
from claude_anyteam.config import Settings
from claude_anyteam.skills_fragment import (
    compose_project_skills_fragment,
    compose_skills_fragment,
    task_text_for_skill_match,
)


REPO_ROOT = Path(__file__).resolve().parent.parent

SKILLS = [
    {
        "name": "diagnose",
        "description": "Read-only claude-anyteam substrate inspector for leads.",
        "when_to_use": "User asks to diagnose claude-anyteam substrate state or manifest cache freshness.",
        "path": "skills/diagnose/SKILL.md",
    },
    {
        "name": "help",
        "description": "Help creating, observing, or managing Agent Teams teammates.",
        "when_to_use": "User asks to create, route, configure, or troubleshoot teammates.",
        "path": "skills/help/SKILL.md",
    },
]


def _task(subject: str = "Diagnose", description: str = "diagnose the team"):
    return SimpleNamespace(
        id="skill-task",
        subject=subject,
        description=description,
        owner="codex-poc-3",
        status="pending",
        blocked_by=[],
    )


def _settings() -> Settings:
    return Settings(
        team_name="protocol-skills-research-2026-05",
        agent_name="codex-poc-3",
        cwd=REPO_ROOT,
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
        app_server=True,
    )


def _success_result() -> CodexResult:
    return CodexResult(
        exit_code=0,
        structured={"files_changed": [], "summary": "done"},
        last_message='{"files_changed": [], "summary": "done"}',
        events=[],
        error=None,
        session_id="session-1",
    )


def test_compose_skills_fragment_matches_diagnose_metadata() -> None:
    fragment = compose_skills_fragment("diagnose the team", SKILLS)

    assert fragment is not None
    # v0.8.4 fragment uses the named header so peers can audit-log injection.
    assert fragment.startswith("## Available Claude Code skills\n")
    # The diagnose entry should be matched and the explicit invoke call
    # instruction must appear so the LLM has a concrete next step.
    assert "- /diagnose: Read-only claude-anyteam substrate inspector for leads." in fragment
    assert (
        "Use when: User asks to diagnose claude-anyteam substrate state"
        in fragment
    )
    assert "mcp_anyteam_invoke_skill('diagnose')" in fragment
    # /help should NOT match the narrow "diagnose the team" task.
    assert "/help" not in fragment


def test_compose_skills_fragment_returns_none_for_unrelated_task() -> None:
    assert compose_skills_fragment("write a haiku", SKILLS) is None


def test_compose_skills_fragment_accepts_source_path_shape() -> None:
    fragment = compose_skills_fragment(
        "inspect manifest cache health",
        [
            {
                "name": "diagnose",
                "description": "Read-only substrate inspector.",
                "when_to_use": "Use for manifest cache health and visibility-degraded diagnostics.",
                "source_path": "/repo/skills/diagnose/SKILL.md",
            }
        ],
    )

    assert fragment is not None
    assert "- /diagnose: Read-only substrate inspector." in fragment
    assert "Use when: Use for manifest cache health and visibility-degraded diagnostics." in fragment
    # The fragment now references the source path as the body's location AND
    # tells the LLM how to fetch via the MCP tool. Both should appear.
    assert "/repo/skills/diagnose/SKILL.md" in fragment
    assert "mcp_anyteam_invoke_skill('diagnose')" in fragment


def test_compose_project_skills_fragment_uses_shared_discovery() -> None:
    """v0.8.4: C uses A's shared `skill_discovery` cache (one skill universe).

    Previously C scanned ``<cwd>/skills/`` only, missing marketplace skills
    that A's wrapper-MCP tools could see. They now share the same discovery
    so cross-backend skill access is consistent across both surfaces.
    """
    fragment = compose_project_skills_fragment("diagnose the team substrate")

    # The in-repo `diagnose` skill must be discoverable from the shared cache
    # regardless of `cwd` (the parameter is now legacy / preserved for
    # backward signature compatibility).
    assert fragment is not None
    assert "/diagnose" in fragment


def test_codex_task_dispatch_injects_matching_skill_fragment() -> None:
    state = codex_loop.LoopState(settings=_settings(), peer_manifest_cache=None)
    prompts_seen: list[str] = []

    with patch.object(
        codex_loop,
        "_execute_task_app_server",
        side_effect=lambda _state, _task, prompt: prompts_seen.append(prompt)
        or _success_result(),
    ):
        result = codex_loop._invoke_codex_for_task(state, _task())

    assert result is not None and result.exit_code == 0
    assert prompts_seen
    # The dispatched prompt must contain the named fragment header AND the
    # explicit invoke instruction. Both are required for the live A+C flow
    # to work end-to-end.
    assert "## Available Claude Code skills" in prompts_seen[0]
    assert "/diagnose" in prompts_seen[0]
    assert "mcp_anyteam_invoke_skill" in prompts_seen[0]


def test_task_text_for_skill_match_handles_strings_and_objects() -> None:
    """The matcher accepts both plain strings (prose path) and task objects."""
    assert task_text_for_skill_match("diagnose the team") == "diagnose the team"

    text = task_text_for_skill_match(_task(subject="Cold email", description="growth"))
    assert "Cold email" in text
    assert "growth" in text

    assert task_text_for_skill_match(None) == ""
    assert task_text_for_skill_match("") == ""
