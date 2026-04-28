from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from claude_teams import server as teams_server
from claude_teams.models import TeammateMember


TEAM = "batch-summary-team"


def _set_roots(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(teams_server.teams, "TEAMS_DIR", tmp_path / "teams")
    monkeypatch.setattr(teams_server.teams, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(teams_server.tasks, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(teams_server.messaging, "TEAMS_DIR", tmp_path / "teams")


def _seed_team(monkeypatch, tmp_path: Path) -> None:
    _set_roots(monkeypatch, tmp_path)
    teams_server.teams.create_team(TEAM, "lead-session")
    teams_server.teams.add_member(
        TEAM,
        TeammateMember(
            agent_id=f"worker@{TEAM}",
            name="worker",
            agent_type="claude-anyteam",
            model="codex",
            prompt="do delegated work",
            color="blue",
            joined_at=0,
            tmux_pane_id="pane-worker",
            cwd="/tmp",
            backend_type="codex-app-server",
        ),
    )


def test_task_batch_summary_emits_visibility_event_and_links_children(
    monkeypatch,
    tmp_path: Path,
):
    _seed_team(monkeypatch, tmp_path)
    parent = teams_server.tasks.create_task(TEAM, "Parent", "delegation root")
    child_unlinked = teams_server.tasks.create_task(TEAM, "Child 1", "delegated")
    child_linked = teams_server.tasks.create_task(
        TEAM,
        "Child 2",
        "delegated",
        parent_task_id=parent.id,
    )

    result = asyncio.run(
        teams_server.mcp.call_tool(
            "task_batch_summary",
            {
                "team_name": TEAM,
                "sender": "worker",
                "parent_task_id": parent.id,
                "child_tasks": [
                    {
                        "task_id": child_unlinked.id,
                        "status": "completed",
                        "session_id": "session-child-1",
                        "stop_reason": "task_complete",
                        "summary": "child one complete",
                    },
                    {
                        "taskId": child_linked.id,
                        "status": "blocked",
                        "stopReason": "needs review",
                    },
                ],
                "summary": "delegated batch ready for review",
            },
        )
    ).structured_content

    assert result["kind"] == "batch_summary"
    assert result["task_id"] == parent.id
    assert result["payload"]["parentTaskId"] == parent.id
    assert result["payload"]["childTaskIds"] == [child_unlinked.id, child_linked.id]
    assert result["payload"]["childTasks"][0] == {
        "taskId": child_unlinked.id,
        "status": "completed",
        "sessionId": "session-child-1",
        "stopReason": "task_complete",
        "summary": "child one complete",
    }

    child_after = teams_server.tasks.get_task(TEAM, child_unlinked.id)
    assert child_after.parent_task_id == parent.id

    event_rows = (
        tmp_path / "teams" / TEAM / "events" / "worker.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert len(event_rows) == 1
    logged = json.loads(event_rows[0])
    assert logged["kind"] == "batch_summary"
    assert logged["payload"] == result["payload"]

    inbox = json.loads(
        (tmp_path / "teams" / TEAM / "inboxes" / "team-lead.json").read_text(
            encoding="utf-8"
        )
    )
    assert inbox[0]["from"] == "worker"
    assert inbox[0]["messageKind"] == "batch_summary"
    body = json.loads(inbox[0]["text"])
    assert body["kind"] == "batch_summary"
    assert body["payload"] == result["payload"]


def test_task_batch_summary_rejects_child_already_linked_to_other_parent(
    monkeypatch,
    tmp_path: Path,
):
    _seed_team(monkeypatch, tmp_path)
    parent = teams_server.tasks.create_task(TEAM, "Parent", "delegation root")
    other_parent = teams_server.tasks.create_task(TEAM, "Other", "different root")
    child = teams_server.tasks.create_task(
        TEAM,
        "Child",
        "delegated",
        parent_task_id=other_parent.id,
    )

    with pytest.raises(Exception, match="already linked to parent task"):
        asyncio.run(
            teams_server.mcp.call_tool(
                "task_batch_summary",
                {
                    "team_name": TEAM,
                    "sender": "worker",
                    "parent_task_id": parent.id,
                    "child_tasks": [{"task_id": child.id, "status": "completed"}],
                    "summary": "should fail",
                },
            )
        )
