"""Coverage for the Kimi plan-prompt builder.

Plan mode is enforced via prompt-embedded schema (NOT the kimi `--plan`
flag, which auto-approves and proceeds in headless per kimi-runtime.md).
The plan_prompt helper is the contract that drives that flow.
"""
from __future__ import annotations

from types import SimpleNamespace

from claude_anyteam.backends.kimi import prompts


def _task(task_id: str = "T1", subject: str = "ship kimi", desc: str = "do the thing"):
    return SimpleNamespace(id=task_id, subject=subject, description=desc)


def test_plan_prompt_mentions_planning_only_no_execution():
    out = prompts.plan_prompt(_task(), tighten=False, agent_name="kimi-alice", team_name="kimi-build")
    assert "Draft a plan" in out
    assert "do not execute" in out.lower()
    assert "kimi-alice" in out
    assert "kimi-build" in out


def test_plan_prompt_includes_task_subject_and_description():
    out = prompts.plan_prompt(
        _task(task_id="T7", subject="rewrite parser", desc="make it streaming"),
        tighten=False,
        agent_name="x",
        team_name="y",
    )
    assert "T7" in out
    assert "rewrite parser" in out
    assert "make it streaming" in out


def test_plan_prompt_tighten_adds_schema_strict_directive():
    soft = prompts.plan_prompt(_task(), tighten=False, agent_name="a", team_name="t")
    hard = prompts.plan_prompt(_task(), tighten=True, agent_name="a", team_name="t")
    assert "PRIOR ATTEMPT FAILED" in hard
    assert "PRIOR ATTEMPT FAILED" not in soft
    assert "schema-compliant JSON" in hard


def test_plan_prompt_returns_only_plan_json_directive():
    out = prompts.plan_prompt(_task(), tighten=False, agent_name="a", team_name="t")
    assert "Return only the plan JSON object" in out


def test_plan_prompt_does_not_invoke_kimi_plan_flag_anywhere():
    """Adapter must never instruct the model to call ``--plan`` or ``ExitPlanMode``;
    those are kimi internals which auto-approve in headless.
    """
    out = prompts.plan_prompt(_task(), tighten=False, agent_name="a", team_name="t")
    assert "--plan" not in out
    assert "ExitPlanMode" not in out


def test_task_prompt_uses_bare_protocol_tools_not_mcp_prefix():
    out = prompts.task_prompt(_task(), agent_name="kimi-alice", team_name="kimi-build")
    assert "send_message" in out
    assert "task_update" in out
    # Critical: prompts must use BARE names, not gemini-style mcp_anyteam_*
    assert "mcp_anyteam_send_message" not in out
    assert "mcp_anyteam_task_update" not in out


def test_prose_reply_prompt_includes_sender_and_body():
    out = prompts.prose_reply_prompt(sender="codex-bob", body="ack", agent_name="kimi-alice", team_name="t")
    assert "codex-bob" in out
    assert "ack" in out
    assert "send_message" in out
