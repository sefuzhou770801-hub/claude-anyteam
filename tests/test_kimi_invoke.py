"""Coverage for the Kimi stream-json parser and session-id capture.

Asserts that the helpers in ``claude_anyteam.backends.kimi.invoke`` correctly
process the empirically captured fixtures under ``tests/fixtures/kimi/``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claude_anyteam.backends.kimi import invoke

FIXTURES = Path(__file__).parent / "fixtures" / "kimi"
PROBES = FIXTURES / "_research_probes"


def _read(name: str, root: Path = FIXTURES) -> str:
    return (root / name).read_text(encoding="utf-8")


def test_simple_assistant_text_returns_final_message():
    events, last, tool_calls = invoke._parse_stdout(_read("simple_assistant_text.jsonl"))
    assert last == "KIMI_SIMPLE_OK"
    assert tool_calls == 0
    assert all(ev.get("type") != "non_json_stdout" for ev in events)


def test_tool_call_lifecycle_counts_tools_and_returns_final_text():
    events, last, tool_calls = invoke._parse_stdout(_read("tool_call_lifecycle.jsonl"))
    assert tool_calls >= 1
    assert "TOOL_SENTINEL=KIMI_SENTINEL_VALUE" in last
    # Lifecycle includes role=tool result line
    assert any(ev.get("role") == "tool" for ev in events if isinstance(ev, dict))


def test_mcp_wrapper_tool_call_increments_count():
    events, last, tool_calls = invoke._parse_stdout(_read("mcp_wrapper_tool_call.jsonl"))
    assert tool_calls >= 1
    assert isinstance(last, str)


def test_session_id_extracted_from_stderr_resume_hint():
    stderr = "\nTo resume this session: kimi -r 3dc086db-76bf-4188-9b53-8892dacbc654\n"
    assert invoke._extract_session_id(stderr) == "3dc086db-76bf-4188-9b53-8892dacbc654"


def test_session_id_absent_when_stderr_lacks_hint():
    assert invoke._extract_session_id("") is None
    assert invoke._extract_session_id("FastMCP AuthlibDeprecationWarning ...") is None


def test_non_json_stdout_lines_are_recorded_not_fatal():
    stdout = '{"role":"assistant","content":"OK"}\nMax number of steps reached: 1\n'
    events, last, tool_calls = invoke._parse_stdout(stdout)
    assert last == "OK"
    assert any(ev.get("type") == "non_json_stdout" for ev in events)


def test_invalid_tool_call_arguments_are_filtered_out():
    stdout = (
        '{"role":"assistant","tool_calls":[{"type":"function",'
        '"id":"t1","function":{"name":"Shell","arguments":"{\\"command"}}]}\n'
        '{"role":"assistant","content":"ABORTED"}\n'
    )
    events, last, tool_calls = invoke._parse_stdout(stdout)
    # The malformed tool-call must NOT be counted as a successful call
    assert tool_calls == 0
    assert last == "ABORTED"


def test_max_steps_overflow_fixture_yields_partial_text_and_terminator():
    """The probed max_steps_overflow fixture stacks two parser landmines:
    a non-JSON terminator line and a tool-call with truncated JSON arguments.
    """
    if not (PROBES / "max_steps_overflow.jsonl").exists():
        pytest.skip("research probe fixture not present")
    events, _last, tool_calls = invoke._parse_stdout(_read("max_steps_overflow.jsonl", PROBES))
    # Truncated tool-call arguments must not be counted as success
    has_terminator = any(ev.get("type") == "non_json_stdout" for ev in events)
    assert has_terminator, "Expected non-JSON terminator line in events"
    # The fixture has at most one well-formed tool call; the broken one is filtered
    assert tool_calls <= 1


def test_content_text_handles_string_form():
    assert invoke._content_text("OK") == "OK"


def test_content_text_handles_list_with_text_and_thought_parts():
    content = [
        {"type": "think", "think": "internal"},
        {"type": "text", "text": "hello "},
        {"type": "text", "text": "world"},
    ]
    assert invoke._content_text(content) == "hello world"


def test_tool_call_name_reads_function_name_or_top_level():
    assert invoke._tool_call_name({"function": {"name": "Shell"}}) == "Shell"
    assert invoke._tool_call_name({"name": "ReadFile"}) == "ReadFile"
    assert invoke._tool_call_name({"unrelated": True}) is None


def test_session_hint_regex_matches_stderr_suffix_form():
    # Verbatim stderr shape captured in kimi-runtime.md §Real stdout sample
    stderr = "\nTo resume this session: kimi -r 9aaa1bbb-2222-3333-4444-555566667777"
    assert invoke._extract_session_id(stderr) == "9aaa1bbb-2222-3333-4444-555566667777"
