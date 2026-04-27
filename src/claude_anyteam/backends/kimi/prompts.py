"""Prompt builders for Kimi CLI teammates.

Kimi exposes MCP tools by their bare declared names; unlike Gemini, there is
no mcp_<server>_<tool> prefix. Prompts mention only the team-protocol tools
needed by the adapter.
"""
from __future__ import annotations


def _tools_text() -> str:
    return (
        "- send_message(to, body, summary?) — send a status update or clarifying question to team-lead or any peer.\n"
        "- task_update(task_id, active_form?, status?) — update your own active_form mid-run; do not set owner or mark completed.\n"
        "- task_create(subject, description) — create a follow-up task if work should be split off.\n"
        "- read_inbox(unread_only?) — read your own inbox for replies.\n"
        "- task_list(), read_config() — read-only team inspection.\n"
    )


def task_prompt(task, agent_name: str, team_name: str) -> str:
    return (
        f"You are {agent_name}, a Kimi CLI teammate on the {team_name} team. Execute the task below.\n\n"
        f"# Subject\n{task.subject}\n\n# Description\n{task.description}\n\n"
        "# MCP tools available\nKimi built-in local tools are available for shell, filesystem, search, and web work. Use the bare anyteam protocol tools below only when useful for teammate coordination; do not call them by default:\n"
        f"{_tools_text()}\nYour current task id is {task.id}.\n\n"
        "# Required response\nProduce the required final JSON object only."
    )


def prose_reply_prompt(sender: str, body: str, agent_name: str, team_name: str) -> str:
    return (
        f"You are {agent_name}, a Kimi CLI teammate on the {team_name} team. "
        f"A teammate named {sender!r} sent you:\n\n{body}\n\n"
        f"Reply briefly and helpfully using send_message(to={sender!r}, body=<reply>). "
        "Plain prose is fine for your local final answer."
    )


def plan_prompt(task, *, tighten: bool, agent_name: str, team_name: str) -> str:
    header = f"You are {agent_name}, a Kimi CLI teammate on the {team_name} team. Draft a plan for task #{task.id}; do not execute work."
    if tighten:
        header += " PRIOR ATTEMPT FAILED: return strictly schema-compliant JSON only."
    return (
        f"{header}\n\n# Subject\n{task.subject}\n\n# Description\n{task.description}\n\n"
        "Protocol tools exist but should not be used while planning. If discussing execution, use Kimi built-in Shell/ReadFile/WriteFile/Grep/Glob tools for execution details, and the bare protocol tools only for teammate coordination.\n"
        "Return only the plan JSON object."
    )
