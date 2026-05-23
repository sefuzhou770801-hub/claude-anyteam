"""Antigravity (``agy``) CLI invocation for claude-anyteam.

agy is a Go CLI with a headless ``--print`` mode that emits plain text on
stdout.  Unlike Kimi or Gemini there is no stream-json, no MCP config
flag, and (today) no resume-hint string on stderr in print mode.  This
module wraps those constraints behind the same ``CodexResult`` shape the
existing loops consume, so the Antigravity loop can stay close to the
Kimi reference.

Tolerant text → structured-output extraction is the only nontrivial
piece: when a schema is requested we pull the *last* JSON object out of
the body (fenced or bare) and validate it.  Anything else is treated as
the final assistant message.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any

from claude_anyteam import logger
from claude_anyteam.auth_preflight import (
    AuthPreflightFailure,
    classify_auth_error,
    subprocess_diagnostic,
)
from claude_anyteam.codex import CodexResult, PLAN_SCHEMA, TASK_COMPLETE_SCHEMA
from claude_anyteam.env import identity_env
from claude_anyteam.headless_visibility import HeadlessTurnVisibility, coerce_stream_text
from claude_anyteam.messages import VisibilityEvent
from claude_anyteam.schema_validation import inline_schema_prompt_fragment, load_schema, parse_and_validate

WRAPPER_SERVER_ALIAS = "anyteam"

# agy currently does not emit a resume hint on stderr in --print mode, but
# we keep a tolerant regex so future versions ("To resume: agy --conversation
# <id>", "conversation_id=<id>", etc.) are captured automatically.  Matching
# is purely opportunistic — absence is normal and not an error.
SESSION_HINT_RES = (
    re.compile(r"To resume(?: this conversation)?:\s*agy\s+(?:-c|--conversation)\s+(\S+)", re.IGNORECASE),
    re.compile(r"conversation[_\s-]?id\s*[=:]\s*([A-Za-z0-9._-]+)", re.IGNORECASE),
)


def _safe_component(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)


def default_antigravity_home(team: str, agent_name: str) -> Path:
    """Return an adapter-owned HOME root for one Antigravity teammate."""
    return Path.home() / ".cache" / "claude-anyteam" / "antigravity" / _safe_component(team) / _safe_component(agent_name)


# Backwards-compatible private spelling (mirrors Kimi/Gemini conventions).
_default_antigravity_home = default_antigravity_home


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


def ensure_antigravity_home(antigravity_home: Path) -> Path:
    antigravity_home.mkdir(parents=True, exist_ok=True)
    ensure_adapter_state(antigravity_home)
    return antigravity_home


def _adapter_state_path(antigravity_home: Path) -> Path:
    return antigravity_home / ".claude-anyteam" / "state.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _default_adapter_state() -> dict[str, Any]:
    return {
        "headless_session_id": None,
        "backend": "headless",
        "updated_at": None,
        "adapter_pid": None,
        "team": None,
        "agent": None,
        "cwd": None,
    }


def ensure_adapter_state(antigravity_home: Path) -> Path:
    path = _adapter_state_path(antigravity_home)
    if not path.exists():
        write_adapter_state(antigravity_home, backend="headless")
    return path


def read_adapter_state(antigravity_home: Path) -> dict[str, Any]:
    defaults = _default_adapter_state()
    path = _adapter_state_path(antigravity_home)
    if not path.exists():
        return dict(defaults)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(defaults)
    if not isinstance(data, dict):
        return dict(defaults)
    merged = dict(defaults)
    merged.update(data)
    return merged


def merge_adapter_state(antigravity_home: Path, **updates: Any) -> Path:
    data = read_adapter_state(antigravity_home)
    data.update(updates)
    data["updated_at"] = _utc_now()
    path = _adapter_state_path(antigravity_home)
    _write_atomic_json(path, data)
    return path


def write_adapter_state(
    antigravity_home: Path,
    *,
    backend: str,
    headless_session_id: str | None = None,
) -> Path:
    previous = read_adapter_state(antigravity_home)
    return merge_adapter_state(
        antigravity_home,
        headless_session_id=(
            headless_session_id
            if headless_session_id is not None
            else previous.get("headless_session_id")
        ),
        backend=backend,
    )


def feature_test(antigravity_binary: str = "agy") -> None:
    resolved = shutil.which(antigravity_binary)
    if not resolved:
        raise RuntimeError(
            f"agy binary not found on PATH (expected {antigravity_binary!r}). "
            "Install Antigravity CLI and ensure it is on PATH."
        )
    try:
        help_env = dict(os.environ)
        help_env["COLUMNS"] = "2000"
        help_out = subprocess.run(
            [antigravity_binary, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=help_env,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise RuntimeError(f"could not probe agy CLI {antigravity_binary!r}: {exc}") from exc
    help_text = (help_out.stdout or "") + (help_out.stderr or "")
    # Required flags for the adapter loop.  ``--continue`` and ``--conversation``
    # back the two resume paths; ``--dangerously-skip-permissions`` lets the
    # loop run unattended; ``--print-timeout`` bounds wall-clock per turn.
    missing = [
        flag
        for flag in (
            "--print",
            "--dangerously-skip-permissions",
            "--print-timeout",
            "--conversation",
            "--add-dir",
        )
        if flag not in help_text
    ]
    if missing:
        raise RuntimeError(
            f"Antigravity CLI is missing required flags {missing}; help head: {help_text[:300].strip()}"
        )
    logger.info(
        "antigravity.version",
        binary=str(Path(resolved).resolve()),
        help_preview=help_text[:200].strip(),
    )


def credential_preflight(
    *,
    antigravity_binary: str = "agy",
    cwd: Path,
    team: str,
    agent_name: str,
    model: str | None = None,
    effort: str | None = None,
    antigravity_home: Path | None = None,
    timeout_s: float = 45.0,
) -> None:
    """Cheap "ping" invocation to surface auth/quota failures before registration.

    agy has no explicit auth subcommand; the only reliable check is a tiny
    ``--print`` round-trip.  We classify the failure with the same shared
    helpers Kimi/Gemini use so the team-lead diagnostics tools render it
    consistently.
    """
    real_home = os.environ.get("HOME")
    home = antigravity_home or default_antigravity_home(team, agent_name)
    ensure_antigravity_home(home)

    args = [
        antigravity_binary,
        "--print",
        "--dangerously-skip-permissions",
        "--print-timeout",
        _fmt_print_timeout(timeout_s),
        "--add-dir",
        str(cwd),
    ]
    if model:
        # agy does not expose a --model flag today; we keep this branch
        # disabled by default but ready for the moment it does.
        logger.debug("antigravity.auth_preflight.model_ignored", model=model)
    args.extend(["-p", "ping"])

    sub_env = dict(os.environ)
    sub_env["HOME"] = str(home)
    if real_home:
        sub_env["CLAUDE_ANYTEAM_REAL_HOME"] = real_home
    sub_env = identity_env(sub_env, team=team, name=agent_name)

    try:
        cwd.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    logger.info(
        "antigravity.auth_preflight.start",
        cwd=str(cwd),
        antigravity_home=str(home),
        model=model,
        effort=effort,
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
        detail = subprocess_diagnostic(
            args,
            returncode=None,
            stdout=coerce_stream_text(getattr(exc, "stdout", None) or getattr(exc, "output", None)),
            stderr=coerce_stream_text(getattr(exc, "stderr", None)),
        )
        raise RuntimeError(
            f"Antigravity auth preflight timed out after {timeout_s}s\n{detail}"
        ) from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise RuntimeError(
            f"could not run Antigravity auth preflight {antigravity_binary!r}: {exc}"
        ) from exc

    diagnostic = subprocess_diagnostic(
        args,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
    if proc.returncode != 0:
        classified = classify_auth_error(diagnostic)
        if classified is not None:
            error_class, reset_after = classified
            raise AuthPreflightFailure(
                backend="antigravity",
                error_class=error_class,
                error_message=diagnostic,
                reset_after_seconds=reset_after,
                cmd=args,
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        raise RuntimeError(f"Antigravity auth preflight exited {proc.returncode}\n{diagnostic}")
    logger.info("antigravity.auth_preflight.ok", model=model)


def _extract_session_id(stderr: str) -> str | None:
    text = stderr or ""
    for pattern in SESSION_HINT_RES:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def _parse_stdout(stdout: str) -> tuple[list[dict[str, Any]], str, int]:
    """Convert plain-text agy output to the (events, last_message, tool_calls) tuple.

    agy emits unstructured text in print mode, so we surface a single
    synthetic ``text`` event for downstream code that introspects
    ``events`` (mirrors what Kimi does with the assistant role) and use
    the trimmed full body as ``last_message``.  Tool-call accounting
    stays at 0 — there is no event stream to count against yet.
    """
    text = (stdout or "").strip()
    if not text:
        return [], "", 0
    events: list[dict[str, Any]] = [{"type": "text", "text": text}]
    return events, text, 0


def _has_json_events(events: list[dict[str, Any]]) -> bool:
    return any(ev.get("type") not in {None, "non_json_stdout"} for ev in events)


def _extract_json_candidate(text: str) -> str:
    """Tolerant JSON extraction from a markdown-ish text body.

    agy may wrap structured output in ```json fences, return bare JSON,
    or surround the object with prose.  We try three strategies in
    order: strip a fenced block, return a bare body, or fall back to
    the last balanced ``{...}`` substring.
    """
    stripped = (text or "").strip()
    if not stripped:
        return ""

    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        fenced = "\n".join(lines).strip()
        if fenced:
            return fenced

    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped

    # Walk from the end and find the outermost balanced JSON object.
    end = stripped.rfind("}")
    while end != -1:
        depth = 0
        in_string = False
        escape = False
        start = -1
        for i in range(end, -1, -1):
            ch = stripped[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "}":
                depth += 1
            elif ch == "{":
                depth -= 1
                if depth == 0:
                    start = i
                    break
        if start != -1:
            candidate = stripped[start : end + 1]
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass
        end = stripped.rfind("}", 0, end)

    return stripped


def _fmt_print_timeout(timeout_s: float) -> str:
    """agy's --print-timeout accepts Go duration strings (e.g. ``10m``, ``45s``).

    We bias toward minute-granularity for long turns and second-granularity
    for short ones so the CLI rendering stays readable.
    """
    total = max(int(round(timeout_s)), 1)
    if total >= 60 and total % 60 == 0:
        return f"{total // 60}m"
    if total >= 60:
        minutes, seconds = divmod(total, 60)
        return f"{minutes}m{seconds}s"
    return f"{total}s"


def _prompt_with_schema(prompt: str, schema_obj: dict[str, Any] | None, *, retry_error: str | None = None) -> str:
    out = prompt
    if schema_obj is not None and "Your final response MUST be a single JSON object matching this schema:" not in out:
        out += "\n\n# Output contract\n" + inline_schema_prompt_fragment(schema_obj)
    if retry_error:
        out += (
            "\n\nPRIOR ATTEMPT FAILED schema validation: "
            + retry_error
            + "\nReturn ONLY the JSON object matching the schema."
        )
    return out


def _run_once(
    prompt: str,
    *,
    cwd: Path,
    schema_obj: dict[str, Any] | None,
    schema_path: Path | None,
    antigravity_binary: str,
    timeout_s: float,
    wrapper_identity: tuple[str, str] | None,
    resume_session_id: str | None,
    model: str | None,
    effort: str | None,
    antigravity_home: Path | None,
    sandbox: bool,
    task_id: str | None,
    event_sink: Callable[[VisibilityEvent], None] | None = None,
    retry_error: str | None = None,
) -> CodexResult:
    team, agent = wrapper_identity or ("default", "antigravity")
    real_home = os.environ.get("HOME")
    home = antigravity_home or default_antigravity_home(team, agent)
    ensure_antigravity_home(home)

    launch_prompt = _prompt_with_schema(prompt, schema_obj, retry_error=retry_error)
    args = [
        antigravity_binary,
        "--print",
        "--dangerously-skip-permissions",
        "--print-timeout",
        _fmt_print_timeout(timeout_s),
        "--add-dir",
        str(cwd),
    ]
    if sandbox:
        args.append("--sandbox")
    if resume_session_id:
        args.extend(["--conversation", resume_session_id])
    args.extend(["-p", launch_prompt])

    sub_env = dict(os.environ)
    sub_env["HOME"] = str(home)
    if real_home:
        sub_env["CLAUDE_ANYTEAM_REAL_HOME"] = real_home
    if wrapper_identity:
        sub_env = identity_env(sub_env, team=team, name=agent)

    logger.info(
        "antigravity.invoke",
        cwd=str(cwd),
        antigravity_home=str(home),
        schema=str(schema_path) if schema_path else None,
        resumed=bool(resume_session_id),
        model=model,
        effort=effort,
        sandbox=sandbox,
    )
    visibility = HeadlessTurnVisibility.start(
        team=team,
        agent=agent,
        backend="antigravity_headless",
        enabled=wrapper_identity is not None,
        cwd=cwd,
        schema=schema_path,
        timeout_s=timeout_s,
        model=model,
        effort=effort,
        resume_session_id=resume_session_id,
        task_id=task_id,
        extra_payload={"sandbox": sandbox},
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
        events, last_message, tool_call_events = _parse_stdout(timeout_stdout)
        captured_session_id = _extract_session_id(timeout_stderr)
        error = f"agy timed out after {timeout_s}s"
        if captured_session_id:
            write_adapter_state(home, backend="headless", headless_session_id=captured_session_id)
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
            extra_payload={"tool_call_event_source": "agy text body (no native event stream)"},
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

    events, last_message, tool_call_events = _parse_stdout(proc.stdout)
    captured_session_id = _extract_session_id(proc.stderr)
    structured: dict[str, Any] | None = None
    error: str | None = None
    if schema_obj is not None:
        parsed, err = parse_and_validate(_extract_json_candidate(last_message), schema_obj)
        structured = parsed
        if err:
            error = f"agy final message failed schema validation: {err}"

    if proc.returncode != 0 and not error:
        diagnostic = (proc.stderr or "").strip() or (proc.stdout or "").strip()
        error = f"agy exited {proc.returncode}; output: {diagnostic[:500]}"

    if captured_session_id:
        write_adapter_state(home, backend="headless", headless_session_id=captured_session_id)

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
        extra_payload={"tool_call_event_source": "agy text body (no native event stream)"},
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


def run(
    prompt: str,
    *,
    cwd: Path,
    schema: Path | None = None,
    antigravity_binary: str = "agy",
    timeout_s: float = 1800.0,
    wrapper_identity: tuple[str, str] | None = None,
    resume_session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    antigravity_home: Path | None = None,
    sandbox: bool = False,
    task_id: str | None = None,
    event_sink: Callable[[VisibilityEvent], None] | None = None,
) -> CodexResult:
    """Single Antigravity invocation.

    Schema-validation retries are owned by the loop layer (matching the
    Kimi/Codex separation of concerns); this function never retries on
    its own.
    """
    schema_obj = load_schema(schema) if schema is not None else None
    return _run_once(
        prompt,
        cwd=cwd,
        schema_obj=schema_obj,
        schema_path=schema,
        antigravity_binary=antigravity_binary,
        timeout_s=timeout_s,
        wrapper_identity=wrapper_identity,
        resume_session_id=resume_session_id,
        model=model,
        effort=effort,
        antigravity_home=antigravity_home,
        sandbox=sandbox,
        task_id=task_id,
        event_sink=event_sink,
    )
