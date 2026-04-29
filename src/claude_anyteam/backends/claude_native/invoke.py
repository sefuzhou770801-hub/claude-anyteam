"""Headless native Claude Code invocation for claude-anyteam."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from claude_anyteam import logger
from claude_anyteam.codex import CodexResult, PLAN_SCHEMA, TASK_COMPLETE_SCHEMA
from claude_anyteam.env import identity_env
from claude_anyteam.headless_visibility import HeadlessTurnVisibility, coerce_stream_text
from claude_anyteam.messages import VisibilityEvent
from claude_anyteam.schema_validation import inline_schema_prompt_fragment, load_schema, parse_and_validate

WRAPPER_SERVER_ALIAS = "anyteam"


def _write_atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _safe_component(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def state_dir(team: str, agent_name: str) -> Path:
    return Path.home() / ".cache" / "claude-anyteam" / "claude-native" / _safe_component(team) / _safe_component(agent_name)


def _wrapper_command_args(wrapper_binary: str = "claude-anyteam-wrapper") -> tuple[str, list[str]]:
    resolved = shutil.which(wrapper_binary)
    if resolved:
        return str(Path(resolved).resolve()), []
    return sys.executable, ["-m", "claude_anyteam.wrapper_server"]


def write_mcp_config(
    root: Path,
    *,
    team: str,
    agent_name: str,
    wrapper_binary: str = "claude-anyteam-wrapper",
) -> Path:
    """Write an adapter-owned Claude MCP config for the anyteam wrapper."""
    env = identity_env(os.environ, team=team, name=agent_name)
    command, prefix_args = _wrapper_command_args(wrapper_binary)
    data = {
        "mcpServers": {
            WRAPPER_SERVER_ALIAS: {
                "command": command,
                "args": [*prefix_args, "--team", team, "--name", agent_name],
                "env": {
                    key: env[key]
                    for key in (
                        "HOME",
                        "PYTHONPATH",
                        "CLAUDE_ANYTEAM_TEAM",
                        "CLAUDE_ANYTEAM_NAME",
                        "CODEX_TEAMMATE_TEAM",
                        "CODEX_TEAMMATE_NAME",
                    )
                    if key in env
                },
            }
        }
    }
    path = root / "anyteam-mcp.json"
    _write_atomic_json(path, data)
    return path


def feature_test(claude_binary: str = "claude") -> None:
    resolved = shutil.which(claude_binary) or (claude_binary if Path(claude_binary).exists() else None)
    if not resolved:
        raise RuntimeError(f"claude binary not found on PATH (expected {claude_binary!r}). Install Claude Code.")
    try:
        help_out = subprocess.run(
            [claude_binary, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise RuntimeError(f"could not probe Claude CLI {claude_binary!r}: {exc}") from exc
    help_text = (help_out.stdout or "") + (help_out.stderr or "")
    missing = [
        flag
        for flag in ("--print", "--verbose", "--output-format", "--mcp-config", "--strict-mcp-config")
        if flag not in help_text
    ]
    if missing:
        raise RuntimeError(f"Claude CLI is missing required flags {missing}")
    logger.info("claude_native.version", binary=str(Path(resolved).resolve()))


def _loads_json_line(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _normalise_tool_name(name: str) -> str:
    if name == "mcp__anyteam__send_message":
        return "send_message"
    if name.startswith("mcp__anyteam__"):
        return name.removeprefix("mcp__anyteam__")
    return name


def _synthetic_tool_event(item: dict[str, Any], *, idx: int) -> dict[str, Any]:
    raw_name = str(item.get("name") or "tool_use")
    input_obj = item.get("input") if isinstance(item.get("input"), dict) else {}
    tool_name = _normalise_tool_name(raw_name)
    synthetic: dict[str, Any] = {
        "type": "tool_use",
        "name": tool_name,
        "raw_tool_name": raw_name,
        "input": input_obj,
        "arguments": input_obj,
        "id": item.get("id"),
        "source": "claude_native_mcp" if raw_name.startswith("mcp__") else "claude_native",
        "raw_content_index": idx,
    }
    if isinstance(input_obj, dict):
        if input_obj.get("to") not in (None, ""):
            synthetic["recipient"] = input_obj.get("to")
            synthetic["target"] = f"to={input_obj.get('to')!r}"
        if input_obj.get("kind") not in (None, ""):
            synthetic["kind"] = input_obj.get("kind")
    return {k: v for k, v in synthetic.items() if v is not None}


def _parse_stdout(stdout: str) -> tuple[list[dict[str, Any]], str, int, str | None]:
    events: list[dict[str, Any]] = []
    last_message = ""
    tool_call_events = 0
    session_id: str | None = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        ev = _loads_json_line(line)
        if ev is None:
            logger.debug("claude_native.nonjson_stdout", line=line[:200])
            events.append({"type": "non_json_stdout", "line": line})
            continue
        events.append(ev)
        if isinstance(ev.get("session_id"), str):
            session_id = ev["session_id"]
        if ev.get("type") == "assistant":
            message = ev.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                text = _content_text(content)
                if text:
                    last_message = text.strip()
                if isinstance(content, list):
                    for idx, item in enumerate(content):
                        if isinstance(item, dict) and item.get("type") == "tool_use":
                            tool_call_events += 1
                            events.append(_synthetic_tool_event(item, idx=idx))
        if ev.get("type") == "result":
            result_text = ev.get("result")
            if isinstance(result_text, str) and result_text.strip():
                last_message = result_text.strip()
            if isinstance(ev.get("session_id"), str):
                session_id = ev["session_id"]
    return events, last_message.strip(), tool_call_events, session_id


def _has_json_events(events: list[dict[str, Any]]) -> bool:
    return any(ev.get("type") != "non_json_stdout" for ev in events)


def _extract_json_candidate(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _embedded_json_object_candidates(text: str) -> list[str]:
    """Return balanced JSON-object substrings embedded in ``text``.

    Native Claude sometimes satisfies the task-complete schema semantically but
    prefixes the final JSON object with a human sentence ("Task #N is complete…
    here is the final output").  Treat that as recoverable only when the
    embedded object itself validates against the requested schema; arbitrary
    trailing prose still fails below.
    """

    candidates: list[str] = []
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for end in range(start, len(text)):
            current = text[end]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : end + 1])
                    break
    return candidates


def _parse_and_validate_final_message(
    text: str,
    schema_obj: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    primary = _extract_json_candidate(text)
    parsed, err = parse_and_validate(primary, schema_obj)
    if parsed is not None:
        return parsed, None

    seen = {primary}
    first_error = err
    for candidate in _embedded_json_object_candidates(primary):
        if candidate in seen:
            continue
        seen.add(candidate)
        parsed, embedded_err = parse_and_validate(candidate, schema_obj)
        if parsed is not None:
            return parsed, None
        if first_error is None:
            first_error = embedded_err
    return None, first_error


def _prompt_with_schema(prompt: str, schema_obj: dict[str, Any] | None, *, retry_error: str | None = None) -> str:
    out = prompt
    if schema_obj is not None and "Your final response MUST be a single JSON object matching this schema:" not in out:
        out += "\n\n# Output contract\n" + inline_schema_prompt_fragment(schema_obj)
    if retry_error:
        out += "\n\nPRIOR ATTEMPT FAILED schema validation: " + retry_error + "\nReturn ONLY the JSON object matching the schema."
    return out


def run(
    prompt: str,
    *,
    cwd: Path,
    schema: Path | None = None,
    claude_binary: str = "claude",
    timeout_s: float = 900.0,
    wrapper_identity: tuple[str, str] | None = None,
    resume_session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    task_id: str | None = None,
    event_sink: Callable[[VisibilityEvent], None] | None = None,
    retry_error: str | None = None,
) -> CodexResult:
    schema_obj = load_schema(schema) if schema is not None else None
    team, agent = wrapper_identity or ("default", "claude")
    root = state_dir(team, agent)
    root.mkdir(parents=True, exist_ok=True)
    mcp_config = write_mcp_config(root, team=team, agent_name=agent)
    launch_prompt = _prompt_with_schema(prompt, schema_obj, retry_error=retry_error)

    args = [
        claude_binary,
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--mcp-config",
        str(mcp_config),
        "--strict-mcp-config",
        "--dangerously-skip-permissions",
        "--add-dir",
        str(cwd),
    ]
    if model:
        args.extend(["--model", model])
    if effort:
        args.extend(["--effort", effort])
    if resume_session_id:
        args.extend(["--resume", resume_session_id])
    args.extend(["-p", launch_prompt])

    sub_env = identity_env(os.environ, team=team, name=agent) if wrapper_identity else dict(os.environ)
    sub_env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    logger.info(
        "claude_native.invoke",
        cwd=str(cwd),
        model=model,
        effort=effort,
        schema=str(schema) if schema else None,
        resumed=bool(resume_session_id),
    )
    visibility = HeadlessTurnVisibility.start(
        team=team,
        agent=agent,
        backend="claude_native",
        enabled=wrapper_identity is not None,
        cwd=cwd,
        schema=schema,
        timeout_s=timeout_s,
        model=model,
        effort=effort,
        resume_session_id=resume_session_id,
        task_id=task_id,
        event_sink=event_sink,
    )
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env=sub_env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        timeout_stdout = coerce_stream_text(getattr(exc, "stdout", None) or getattr(exc, "output", None))
        timeout_stderr = coerce_stream_text(getattr(exc, "stderr", None))
        events, last_message, tool_call_events, captured_session_id = _parse_stdout(timeout_stdout)
        error = f"claude timed out after {timeout_s}s"
        if timeout_stderr.strip():
            error += f"; stderr: {timeout_stderr.strip()[:500]}"
        visibility.terminal(
            success=False,
            exit_code=124,
            error=error,
            events=events,
            tool_call_events=tool_call_events,
            last_message=last_message,
            structured=False,
            partial_events_available=_has_json_events(events),
            session_id=captured_session_id,
            error_class="turn_timeout",
            extra_payload={"tool_call_event_source": "claude_native"},
        )
        return CodexResult(
            exit_code=124,
            structured=None,
            last_message=last_message,
            events=events,
            error=error,
            tool_call_events=tool_call_events,
            session_id=captured_session_id,
        )

    events, last_message, tool_call_events, captured_session_id = _parse_stdout(proc.stdout)
    structured: dict[str, Any] | None = None
    error: str | None = None
    if schema_obj is not None:
        parsed, err = _parse_and_validate_final_message(last_message, schema_obj)
        structured = parsed
        if err:
            error = f"claude final message failed schema validation: {err}"

    if proc.returncode != 0 and not error:
        diagnostic = (proc.stderr or "").strip() or (proc.stdout or "").strip()
        error = f"claude exited {proc.returncode}; output: {diagnostic[:500]}"
    elif proc.stderr.strip():
        logger.debug("claude_native.stderr", stderr=proc.stderr.strip()[:500])

    success = proc.returncode == 0 and error is None
    visibility.terminal(
        success=success,
        exit_code=proc.returncode,
        error=error,
        events=events,
        tool_call_events=tool_call_events,
        last_message=last_message,
        structured=structured is not None,
        partial_events_available=_has_json_events(events),
        session_id=captured_session_id,
        extra_payload={"tool_call_event_source": "claude_native"},
    )
    return CodexResult(
        exit_code=proc.returncode,
        structured=structured,
        last_message=last_message,
        events=events,
        error=error,
        tool_call_events=tool_call_events,
        session_id=captured_session_id,
    )
