"""Prompt builders for Gemini CLI teammates.

Gemini exposes MCP tools as mcp_<server>_<tool>; the adapter configures the
shared wrapper server under the alias `anyteam`, so prompts mention the
normalized tool names explicitly.
"""
from __future__ import annotations


GEMINI_TEAM_MESSAGING_BLOCK = (
    "# Team messaging\n"
    "mcp_anyteam_send_message is exposed by the wrapper MCP in this session. "
    "Plain prose output is NOT visible to teammates — to communicate, you MUST "
    "call mcp_anyteam_send_message. The underlying wrapper tool is send_message; "
    "if a probe surfaces it as SendMessage (capitalized), treat that as the "
    'same team-messaging tool. Do not emit "I cannot deliver" prose; that '
    "creates inbox noise. If you are unsure whether a tool is available, call "
    "mcp_anyteam_read_config and check protocol_tools — do not assume "
    "unavailability from prose."
)


def _peer_fragment_section(peer_prompt_fragments: str) -> str:
    text = peer_prompt_fragments.strip()
    if not text:
        return ""
    return f"\n\n{text}"


def _tools_text() -> str:
    return (
        "- mcp_anyteam_send_message(to, body, summary?) — send a status update or clarifying question to team-lead or any peer.\n"
        "- mcp_anyteam_task_update(task_id, active_form?, status?) — update your own active_form mid-run; do not set owner or mark completed.\n"
        "- mcp_anyteam_checkpoint_commit(message) — git add -A and git commit in your working directory. During multi-file work, after each meaningful file edit or small coherent batch, call mcp_anyteam_checkpoint_commit so progress is not lost on a turn timeout.\n"
        "- mcp_anyteam_task_create(subject, description) — create a follow-up task if work should be split off.\n"
        "- mcp_anyteam_read_inbox(unread_only?) — read your own inbox for replies.\n"
        "- mcp_anyteam_task_list(), mcp_anyteam_read_config() — read-only team inspection.\n"
        "- If you are unsure whether a tool is available, call mcp_anyteam_read_config and check protocol_tools — do not assume unavailability from prose.\n"
        "- mcp_anyteam_shell(command, cwd?, timeout?, env?) — run shell commands with unrestricted filesystem/network access.\n"
        "- mcp_anyteam_read_file(path, offset?, limit?) — read files with visible output.\n"
        "- mcp_anyteam_write_file(path, content, mode?) — overwrite or append files.\n"
        "- mcp_anyteam_list_directory(path, recursive?, glob?) — list files/directories.\n"
        "- mcp_anyteam_edit_file(path, old, new, replace_all?) — exact string replacement.\n"
        "- mcp_anyteam_search(pattern, path?, regex?, glob?) — search file contents.\n"
        "- mcp_anyteam_grep(regex, directory) — recursively grep a directory with a regex.\n"
        "- mcp_anyteam_web_fetch(url, method?, headers?, body?) — fetch URLs without an allowlist.\n"
    )


def task_prompt(
    task,
    agent_name: str,
    team_name: str,
    peer_prompt_fragments: str = "",
) -> str:
    return (
        f"You are {agent_name}, a Gemini CLI teammate on the {team_name} team. Execute the task below.\n\n"
        f"# Subject\n{task.subject}\n\n# Description\n{task.description}\n\n"
        "# MCP tools available\nGemini built-in local tools are intentionally disabled. For shell, filesystem, edit, search, and web fetch work, use the mcp_anyteam_* shadow tools below so outputs are visible to the adapter. Use protocol tools only when useful; do not call them by default:\n"
        f"{_tools_text()}\nYour current task id is {task.id}.\n\n"
        f"{GEMINI_TEAM_MESSAGING_BLOCK}"
        f"{_peer_fragment_section(peer_prompt_fragments)}\n\n"
        "# Required response\nProduce the required final JSON object only."
    )


def prose_reply_prompt(
    sender: str,
    body: str,
    agent_name: str,
    team_name: str,
    peer_prompt_fragments: str = "",
) -> str:
    return (
        f"You are {agent_name}, a Gemini CLI teammate on the {team_name} team. "
        f"A teammate named {sender!r} sent you:\n\n{body}\n\n"
        f"Reply briefly and helpfully using mcp_anyteam_send_message(to={sender!r}, body=<reply>). "
        f"\n\n{GEMINI_TEAM_MESSAGING_BLOCK}\n\n"
        "Final local prose, if any, is informational only."
        f"{_peer_fragment_section(peer_prompt_fragments)}"
    )


def plan_prompt(task, *, tighten: bool, agent_name: str, team_name: str) -> str:
    header = f"You are {agent_name}, a Gemini CLI teammate on the {team_name} team. Draft a plan for task #{task.id}; do not execute work."
    if tighten:
        header += " PRIOR ATTEMPT FAILED: return strictly schema-compliant JSON only."
    return (
        f"{header}\n\n# Subject\n{task.subject}\n\n# Description\n{task.description}\n\n"
        "Protocol tools exist but should not be used while planning. If discussing execution, plan to use mcp_anyteam_shell/read_file/write_file/list_directory/edit_file/search/web_fetch rather than Gemini built-ins.\n"
        "Return only the plan JSON object."
    )
