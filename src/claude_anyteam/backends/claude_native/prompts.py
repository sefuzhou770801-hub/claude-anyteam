"""Prompt builders for native Claude Code headless teammates."""
from __future__ import annotations


CLAUDE_TEAM_MESSAGING_BLOCK = (
    "# Team messaging\n"
    "The wrapper MCP server is mounted as `anyteam`; Claude Code exposes those "
    "tools with names like `mcp__anyteam__send_message`, "
    "`mcp__anyteam__task_update`, `mcp__anyteam__read_config`, and "
    "`mcp__anyteam__task_list`. Plain prose output is NOT visible to teammates — "
    "to communicate, you MUST call `mcp__anyteam__send_message`. If you are "
    "unsure which tools are available, call `mcp__anyteam__read_config` and "
    "check `protocol_tools`; do not assume unavailability from prose."
)


def _peer_fragment_section(peer_prompt_fragments: str) -> str:
    text = peer_prompt_fragments.strip()
    if not text:
        return ""
    return f"\n\n{text}"


def _tools_text() -> str:
    return (
        "- mcp__anyteam__send_message(to, body, summary?, kind?) — send a status update or clarifying question to team-lead or any peer.\n"
        "- mcp__anyteam__task_update(task_id, active_form?, status?) — update your own active_form mid-run; do not set owner or mark completed unless the task is genuinely done.\n"
        "- mcp__anyteam__checkpoint_commit(message) — git add -A and git commit in your working directory. During multi-file work, after each meaningful file edit or small coherent batch, call this so progress is durable.\n"
        "- mcp__anyteam__task_create(subject, description) — create a follow-up task if work should be split off.\n"
        "- mcp__anyteam__read_inbox(unread_only?) — read your own inbox for replies.\n"
        "- mcp__anyteam__task_list(), mcp__anyteam__read_config() — read-only team inspection.\n"
    )


def task_prompt(
    task,
    agent_name: str,
    team_name: str,
    peer_prompt_fragments: str = "",
) -> str:
    return (
        f"You are {agent_name}, a native Claude Code teammate on the {team_name} team. "
        "Execute the task below using Claude Code's native tools.\n\n"
        f"# Subject\n{task.subject}\n\n# Description\n{task.description}\n\n"
        "# MCP tools available\n"
        "Use Claude Code built-in Bash/Edit/Read/Grep/Glob/Write tools for local work. "
        "Use the anyteam MCP tools for teammate coordination and task state:\n"
        f"{_tools_text()}"
        f"Your current task id is {task.id}.\n\n"
        f"{CLAUDE_TEAM_MESSAGING_BLOCK}"
        f"{_peer_fragment_section(peer_prompt_fragments)}\n\n"
        "# Required response\n"
        "Produce the required final JSON object only."
    )


def prose_reply_prompt(
    sender: str,
    body: str,
    agent_name: str,
    team_name: str,
    peer_prompt_fragments: str = "",
) -> str:
    return (
        f"You are {agent_name}, a native Claude Code teammate on the {team_name} team. "
        f"A teammate named {sender!r} sent you:\n\n{body}\n\n"
        f"Reply briefly and helpfully using `mcp__anyteam__send_message(to={sender!r}, body=<reply>)`. "
        "Do not execute code unless explicitly asked.\n\n"
        f"{CLAUDE_TEAM_MESSAGING_BLOCK}\n\n"
        "Final local prose, if any, is informational only; delivery to the teammate must happen via the MCP tool."
        f"{_peer_fragment_section(peer_prompt_fragments)}"
    )
