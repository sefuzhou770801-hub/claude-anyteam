from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from claude_anyteam import prompts as codex_prompts
from claude_anyteam.backends.gemini import prompts as gemini_prompts
from claude_anyteam.backends.kimi import prompts as kimi_prompts
from claude_anyteam.wrapper_server import build_server
from claude_teams import server as teams_server
from claude_teams.models import LeadMember, TeammateMember, TeamConfig


TEAM = "peer-dm-matrix"
BACKEND_PEERS = (
    ("codex-app-server", "codex-app-server"),
    ("codex-exec", "codex-exec"),
    ("gemini-acp", "gemini-acp"),
    ("gemini-headless", "gemini-headless"),
    ("kimi-headless", "kimi-headless"),
)


def _member(name: str, backend_type: str, color: str) -> TeammateMember:
    return TeammateMember(
        agent_id=f"{name}@{TEAM}",
        name=name,
        agent_type="claude-anyteam",
        model="test-model",
        prompt=f"{backend_type} routed teammate",
        color=color,
        joined_at=0,
        tmux_pane_id=f"pane-{name}",
        cwd="/tmp",
        backend_type=backend_type,
    )


def _write_team(root: Path) -> Path:
    team_dir = root / "teams" / TEAM
    (team_dir / "inboxes").mkdir(parents=True)
    cfg = TeamConfig(
        name=TEAM,
        description="09 R21/W3 five-backend peer-DM matrix",
        created_at=0,
        lead_agent_id=f"team-lead@{TEAM}",
        lead_session_id="lead-session",
        members=[
            LeadMember(
                agent_id=f"team-lead@{TEAM}",
                name="team-lead",
                agent_type="team-lead",
                model="claude-opus",
                joined_at=0,
                cwd="/tmp",
            ),
            *(
                _member(name, backend_type, color)
                for (name, backend_type), color in zip(
                    BACKEND_PEERS,
                    ("red", "blue", "green", "purple", "orange"),
                    strict=True,
                )
            ),
        ],
    )
    (team_dir / "config.json").write_text(
        json.dumps(cfg.model_dump(by_alias=True, exclude_none=True), indent=2),
        encoding="utf-8",
    )
    return team_dir


def _call_wrapper_send(sender: str, recipient: str) -> dict:
    mcp = build_server(["--team", TEAM, "--name", sender])
    result = asyncio.run(
        mcp.call_tool(
            "send_message",
            {
                "to": recipient,
                "body": f"matrix hello from {sender} to {recipient}",
                "summary": f"dm:{sender}->{recipient}",
            },
        )
    )
    return result.structured_content


def test_five_backend_wrapper_peer_dm_matrix_delivers_exactly_once(monkeypatch, tmp_path):
    """09 R21 / W3: every routed backend identity can DM every peer.

    This intentionally exercises the same wrapper MCP surface used by Codex
    App Server, Codex exec, Gemini ACP, Gemini headless, and Kimi headless.
    Each sender→recipient pair must land as one inbox entry — no duplicate
    canned fallback and no "I don't have access to send_message" prose.
    """

    team_dir = _write_team(tmp_path)
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_teams.TEAMS_DIR", tmp_path / "teams")
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_messaging.TEAMS_DIR", tmp_path / "teams")

    peer_names = [name for name, _backend in BACKEND_PEERS]
    for sender in peer_names:
        for recipient in peer_names:
            if sender == recipient:
                continue
            assert _call_wrapper_send(sender, recipient) == {
                "delivered_to": recipient,
                "sender": sender,
            }

    for recipient in peer_names:
        inbox = json.loads((team_dir / "inboxes" / f"{recipient}.json").read_text(encoding="utf-8"))
        assert len(inbox) == len(peer_names) - 1
        assert {m["from"] for m in inbox} == set(peer_names) - {recipient}
        for sender in set(peer_names) - {recipient}:
            matches = [m for m in inbox if m["from"] == sender]
            assert len(matches) == 1, f"{recipient} should receive one DM from {sender}, not {len(matches)}"
            text = matches[0]["text"]
            assert "matrix hello" in text
            assert "I don't have access to send_message" not in text
            assert "I don't have access to a `send_message` MCP tool" not in text
            assert "Teammates can only send direct messages to team-lead" not in text


def test_full_claude_teams_server_allows_peer_to_peer_dm(monkeypatch, tmp_path):
    """Substrate regression for W3: full `send_message` no longer rejects peer→peer."""

    team_dir = _write_team(tmp_path)
    monkeypatch.setattr(teams_server.teams, "TEAMS_DIR", tmp_path / "teams")
    monkeypatch.setattr(teams_server.messaging, "TEAMS_DIR", tmp_path / "teams")

    result = asyncio.run(
        teams_server.mcp.call_tool(
            "send_message",
            {
                "team_name": TEAM,
                "type": "message",
                "sender": "codex-app-server",
                "recipient": "gemini-acp",
                "content": "server-level peer DM",
                "summary": "substrate peer dm",
            },
        )
    )

    assert result.structured_content["success"] is True
    inbox = json.loads((team_dir / "inboxes" / "gemini-acp.json").read_text(encoding="utf-8"))
    assert len(inbox) == 1
    assert inbox[0]["from"] == "codex-app-server"
    assert "server-level peer DM" in inbox[0]["text"]
    assert inbox[0]["color"] == "red"


def test_prompts_advertise_peer_dm_not_lead_only():
    """Prompt audit for W3: no backend teaches "you can only message team-lead"."""

    task = SimpleNamespace(id="1", subject="s", description="d")
    rendered = "\n\n".join(
        [
            codex_prompts.v7_task_prompt(task, agent_name="codex-app-server", team_name=TEAM),
            codex_prompts.v7_prose_reply_prompt(
                sender="gemini-acp",
                body="hello",
                agent_name="codex-exec",
                team_name=TEAM,
            ),
            gemini_prompts.task_prompt(task, agent_name="gemini-acp", team_name=TEAM),
            gemini_prompts.prose_reply_prompt(
                sender="codex-app-server",
                body="hello",
                agent_name="gemini-headless",
                team_name=TEAM,
            ),
            kimi_prompts.task_prompt(task, agent_name="kimi-headless", team_name=TEAM),
            kimi_prompts.prose_reply_prompt(
                sender="codex-exec",
                body="hello",
                agent_name="kimi-headless",
                team_name=TEAM,
            ),
        ]
    ).lower()

    assert "only message team-lead" not in rendered
    assert "only send direct messages to team-lead" not in rendered
    assert "teammates can only send direct messages to team-lead" not in rendered
    assert "any peer" in rendered
