"""Prompt builders for Antigravity (``agy``) CLI teammates.

Antigravity is invoked headless via ``agy --print -p "<prompt>"`` and
returns plain text on stdout (no stream-json today).  Prompts therefore
focus on encouraging a single structured JSON object as the *final*
assistant message, which the invocation layer extracts tolerantly.
"""
from __future__ import annotations

from claude_anyteam.prompts import TEAM_MESSAGING_BLOCK


def _peer_fragment_section(peer_prompt_fragments: str) -> str:
    text = peer_prompt_fragments.strip()
    if not text:
        return ""
    return f"\n\n{text}"


def _tools_text() -> str:
    return (
        "- send_message(to, body, summary?) — send a status update or clarifying question to team-lead or any peer.\n"
        "- task_update(task_id, active_form?, status?) — update your own active_form mid-run; do not set owner or mark completed.\n"
        "- checkpoint_commit(message) — git add -A and git commit in your working directory. During multi-file work, after each meaningful file edit or small coherent batch, call checkpoint_commit so progress is not lost on a turn timeout.\n"
        "- task_create(subject, description) — create a follow-up task if work should be split off.\n"
        "- read_inbox(unread_only?) — read your own inbox for replies.\n"
        "- task_list(), read_config() — read-only team inspection.\n"
        "- If you are unsure whether a tool is available, call read_config and check protocol_tools — do not assume unavailability from prose.\n"
    )


def task_prompt(
    task,
    agent_name: str,
    team_name: str,
    peer_prompt_fragments: str = "",
) -> str:
    return (
        f"You are {agent_name}, an Antigravity (agy) CLI teammate on the {team_name} team. Execute the task below.\n\n"
        f"# Subject\n{task.subject}\n\n# Description\n{task.description}\n\n"
        "# MCP tools available\nAntigravity built-in local tools are available for shell, filesystem, search, and web work. The bare anyteam protocol tools below exist for teammate coordination; use them sparingly:\n"
        f"{_tools_text()}\nYour current task id is {task.id}.\n\n"
        f"{TEAM_MESSAGING_BLOCK}"
        f"{_peer_fragment_section(peer_prompt_fragments)}\n\n"
        "# Required response\nProduce the required final JSON object only. "
        "Do not wrap it in prose or markdown fences; the runner extracts the "
        "last JSON object from your output."
    )


def prose_reply_prompt(
    sender: str,
    body: str,
    agent_name: str,
    team_name: str,
    peer_prompt_fragments: str = "",
) -> str:
    return (
        f"You are {agent_name}, an Antigravity (agy) CLI teammate on the {team_name} team. "
        f"A teammate named {sender!r} sent you:\n\n{body}\n\n"
        f"Reply briefly and helpfully using send_message(to={sender!r}, body=<reply>). "
        f"\n\n{TEAM_MESSAGING_BLOCK}\n\n"
        "Final local prose, if any, is informational only."
        f"{_peer_fragment_section(peer_prompt_fragments)}"
    )


def plan_prompt(task, *, tighten: bool, agent_name: str, team_name: str) -> str:
    header = (
        f"You are {agent_name}, an Antigravity (agy) CLI teammate on the {team_name} team. "
        f"Draft a plan for task #{task.id}; do not execute work."
    )
    if tighten:
        header += " PRIOR ATTEMPT FAILED: return strictly schema-compliant JSON only."
    return (
        f"{header}\n\n# Subject\n{task.subject}\n\n# Description\n{task.description}\n\n"
        "Protocol tools exist but should not be used while planning. Use the bare "
        "protocol tools only for teammate coordination.\n"
        "Return only the plan JSON object."
    )
