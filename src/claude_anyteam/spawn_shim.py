"""claude-anyteam spawn shim.

This module is intentionally stdlib-only so it can be imported standalone.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

from .env import (
    ALLOW_BARE_PREFIX_ENV,
    ANTIGRAVITY_BINARY_ENV,
    ANTIGRAVITY_SHIM_MATCH_ENV,
    BINARY_ENV,
    CLAUDE_SHIM_MATCH_ENV,
    EVENT_FIFO_ENV,
    LEGACY_BINARY_ENV,
    GEMINI_BINARY_ENV,
    GEMINI_SHIM_MATCH_ENV,
    KIMI_BINARY_ENV,
    KIMI_SHIM_MATCH_ENV,
    LEGACY_NATIVE_CLAUDE_ENV,
    LEGACY_SHIM_MATCH_ENV,
    NATIVE_CLAUDE_ENV,
    SHIM_MATCH_ENV,
    env_first,
)

# Opt-out for the Ink TUI viewer fan-out. Default on. Set to "0"/"false" to
# skip launching the viewer and exec the adapter directly (the pre-viewer
# behavior). The viewer is a cosmetic live mirror; this gives operators an
# escape hatch if the cmux split shouldn't be taken over by the TUI.
VIEWER_ENABLE_ENV = "CLAUDE_ANYTEAM_CODEX_VIEWER"
VIEWER_BINARY_ENV = "CLAUDE_ANYTEAM_CODEX_VIEWER_BIN"

DEFAULT_CODEX_MATCH = r"^codex-"
DEFAULT_CLAUDE_MATCH = r"^claude-"
DEFAULT_GEMINI_MATCH = r"^gemini-"
DEFAULT_KIMI_MATCH = r"^kimi-"
DEFAULT_ANTIGRAVITY_MATCH = r"^antigravity-"
PRIMARY_BINARY = "claude-anyteam"
LEGACY_BINARY = "codex-teammate"
GEMINI_BINARY = "gemini-anyteam"
KIMI_BINARY = "kimi-anyteam"
ANTIGRAVITY_BINARY = "antigravity-anyteam"
UNPARSEABLE_AGENT_CONFIG_PATH = "<unparseable>"


@dataclass
class ParsedArgs:
    agent_name: str | None = None
    team_name: str | None = None
    plan_mode_required: bool = False
    saw_agent_name_flag: bool = False
    saw_team_name_flag: bool = False

    @property
    def saw_identity_flags(self) -> bool:
        return self.saw_agent_name_flag or self.saw_team_name_flag



def _parse_args(argv: list[str]) -> ParsedArgs:
    parsed = ParsedArgs()
    i = 0
    while i < len(argv):
        arg = argv[i]

        if arg == "--agent-name":
            parsed.saw_agent_name_flag = True
            if i + 1 < len(argv):
                parsed.agent_name = argv[i + 1]
                i += 2
                continue
            i += 1
            continue

        if arg.startswith("--agent-name="):
            parsed.saw_agent_name_flag = True
            parsed.agent_name = arg.split("=", 1)[1]
            i += 1
            continue

        if arg == "--team-name":
            parsed.saw_team_name_flag = True
            if i + 1 < len(argv):
                parsed.team_name = argv[i + 1]
                i += 2
                continue
            i += 1
            continue

        if arg.startswith("--team-name="):
            parsed.saw_team_name_flag = True
            parsed.team_name = arg.split("=", 1)[1]
            i += 1
            continue

        if arg == "--plan-mode-required":
            parsed.plan_mode_required = True

        i += 1

    return parsed



def _resolve_binary(default_name: str, *env_vars: str, fallback_name: str | None = None) -> str | None:
    override = env_first(os.environ, *env_vars)
    if override:
        return shutil.which(override) or override
    return shutil.which(default_name) or (shutil.which(fallback_name) if fallback_name else None)


_AGENT_CONFIG_KEYS = (
    "model",
    "effort",
    "turn_timeout_s",
    "non_progress_warn_s",
    "non_progress_interrupt_s",
    "wrapper_tool_failure_window_s",
)


def _agent_config_path(team_name: str, agent_name: str) -> str:
    return os.path.join(
        os.path.expanduser("~"),
        ".claude",
        "teams",
        team_name,
        "agents",
        f"{agent_name}.json",
    )


def _agent_config_path_for_event(
    team_name: str | None, agent_name: str | None
) -> str:
    if team_name and agent_name:
        return _agent_config_path(team_name, agent_name)
    return UNPARSEABLE_AGENT_CONFIG_PATH


def _env_flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _agent_config_exists(team_name: str | None, agent_name: str | None) -> bool:
    if not team_name or not agent_name:
        return False
    return os.path.exists(_agent_config_path(team_name, agent_name))


def _team_agent_suggestion(parsed: ParsedArgs) -> str:
    agent = parsed.agent_name or "<agent-name>"
    team = parsed.team_name or "<team-name>"
    return (
        f"claude-anyteam team-agent {agent} --team {team} "
        "--model <model> --effort <effort>"
    )


def _refuse_bare_routed_prefix(route: str, parsed: ParsedArgs) -> int:
    config_path = _agent_config_path_for_event(parsed.team_name, parsed.agent_name)
    suggestion = _team_agent_suggestion(parsed)
    message = (
        f"Refusing bare {route} routed teammate {parsed.agent_name!r}: no "
        "per-teammate config file was found"
    )
    if config_path != UNPARSEABLE_AGENT_CONFIG_PATH:
        message += f" at {config_path}"
    message += (
        f". Run `{suggestion}` before Agent(...), or set "
        f"{ALLOW_BARE_PREFIX_ENV}=1 to intentionally use adapter defaults."
    )
    record: dict[str, object] = {
        "event": "spawn_shim.bare_prefix_refused",
        "route": route,
        "agent_name": parsed.agent_name,
        "team_name": parsed.team_name,
        "error_class": "missing_agent_config",
        "error_detail": message,
        "message": message,
        "config_path": config_path,
        "suggested_command": suggestion,
        "override_env": ALLOW_BARE_PREFIX_ENV,
        "override_hint": (
            f"Set {ALLOW_BARE_PREFIX_ENV}=1 only when you intentionally want "
            "a routed teammate to start with adapter defaults and no "
            "team-agent config."
        ),
        "issue": "#48",
    }
    sys.stderr.write(json.dumps(record, sort_keys=True) + "\n")
    sys.stderr.flush()
    return 2


def _emit_bare_prefix_override_event(route: str, parsed: ParsedArgs) -> None:
    config_path = _agent_config_path_for_event(parsed.team_name, parsed.agent_name)
    message = (
        f"Allowing bare {route} routed teammate {parsed.agent_name!r} because "
        f"{ALLOW_BARE_PREFIX_ENV}=1 is set and no per-teammate config file "
        "was found"
    )
    if config_path != UNPARSEABLE_AGENT_CONFIG_PATH:
        message += f" at {config_path}"
    record: dict[str, object] = {
        "event": "spawn_shim.bare_prefix_allowed_via_override",
        "route": route,
        "agent_name": parsed.agent_name,
        "team_name": parsed.team_name,
        "config_path": config_path,
        "override_env": ALLOW_BARE_PREFIX_ENV,
        "message": message,
        "issue": "#48",
    }
    sys.stderr.write(json.dumps(record, sort_keys=True) + "\n")
    sys.stderr.flush()


def _maybe_refuse_bare_routed_prefix(route: str, parsed: ParsedArgs) -> int | None:
    if _agent_config_exists(parsed.team_name, parsed.agent_name):
        return None
    # Azir fork: always allow bare prefix — env var not reliably forwarded to teammate spawn
    _emit_bare_prefix_override_event(route, parsed)
    return None


def _load_agent_config(team_name: str | None, agent_name: str | None) -> dict[str, str]:
    """Read per-teammate overrides from the team's agents directory.

    Silently returns an empty dict on unreadable file, malformed JSON, or
    non-object content. Missing routed-adapter config is rejected before this
    helper runs unless CLAUDE_ANYTEAM_ALLOW_BARE_PREFIX=1 is set; with that
    explicit override, teammates still start with whatever defaults the
    adapter picks up from env or backend config.

    Only whitelisted keys are forwarded; unknown keys are ignored.
    """
    if not team_name or not agent_name:
        return {}
    path = _agent_config_path(team_name, agent_name)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(
            json.dumps(
                {
                    "event": "spawn_shim.agent_config_error",
                    "path": path,
                    "error": str(exc),
                },
                sort_keys=True,
            )
            + "\n"
        )
        sys.stderr.flush()
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in _AGENT_CONFIG_KEYS:
        value = raw.get(key)
        # Accept both string and numeric values; stringify uniformly so the
        # downstream argv builder can pass it as a CLI flag without further
        # branching.
        if isinstance(value, str) and value:
            out[key] = value
        elif isinstance(value, (int, float)):
            out[key] = str(value)
    return out



def _resolve_current_invocation(argv0: str) -> str | None:
    if not argv0:
        return None
    if os.path.dirname(argv0):
        candidate = argv0
    else:
        candidate = shutil.which(argv0)
    if not candidate:
        return None
    return os.path.realpath(candidate)



def _resolve_native_claude(argv0: str) -> str | None:
    current = _resolve_current_invocation(argv0)
    override = env_first(os.environ, NATIVE_CLAUDE_ENV, LEGACY_NATIVE_CLAUDE_ENV)
    if override:
        resolved = shutil.which(override) or override
        if not (current and os.path.realpath(resolved) == current):
            return resolved

    candidate = shutil.which("claude")
    if candidate and os.path.realpath(candidate) != current:
        return candidate

    for directory in os.get_exec_path():
        candidate = os.path.join(directory, "claude")
        if not os.path.isfile(candidate):
            continue
        if not os.access(candidate, os.X_OK):
            continue
        if os.path.realpath(candidate) == current:
            continue
        return candidate

    return None



def _route_match(parsed: ParsedArgs, *, env_name: str, legacy_env_name: str | None, default: str) -> bool:
    if not parsed.agent_name:
        return False
    names = (env_name, legacy_env_name) if legacy_env_name else (env_name,)
    pattern = env_first(os.environ, *names, default=default) or default
    try:
        return re.search(pattern, parsed.agent_name) is not None
    except re.error as exc:
        raise SystemExit(f"Invalid {env_name} regex: {pattern!r}: {exc}") from exc


def _codex_route(parsed: ParsedArgs) -> bool:
    return _route_match(parsed, env_name=SHIM_MATCH_ENV, legacy_env_name=LEGACY_SHIM_MATCH_ENV, default=DEFAULT_CODEX_MATCH)


def _claude_route(parsed: ParsedArgs) -> bool:
    return _route_match(parsed, env_name=CLAUDE_SHIM_MATCH_ENV, legacy_env_name=None, default=DEFAULT_CLAUDE_MATCH)


def _gemini_route(parsed: ParsedArgs) -> bool:
    return _route_match(parsed, env_name=GEMINI_SHIM_MATCH_ENV, legacy_env_name=None, default=DEFAULT_GEMINI_MATCH)


def _kimi_route(parsed: ParsedArgs) -> bool:
    return _route_match(parsed, env_name=KIMI_SHIM_MATCH_ENV, legacy_env_name=None, default=DEFAULT_KIMI_MATCH)


def _antigravity_route(parsed: ParsedArgs) -> bool:
    return _route_match(
        parsed,
        env_name=ANTIGRAVITY_SHIM_MATCH_ENV,
        legacy_env_name=None,
        default=DEFAULT_ANTIGRAVITY_MATCH,
    )



def _log_dispatch(
    route: str,
    agent_name: str | None,
    binary: str | None,
    agent_config: dict[str, str] | None = None,
) -> None:
    record: dict[str, object] = {
        "event": "spawn_shim.dispatch",
        "route": route,
        "agent_name": agent_name,
        "binary": binary,
    }
    if agent_config:
        record["agent_config"] = dict(agent_config)
    sys.stderr.write(json.dumps(record, sort_keys=True) + "\n")
    sys.stderr.flush()



def _require_binary(binary: str | None, label: str) -> str:
    if binary:
        return binary
    raise SystemExit(f"Unable to resolve {label} binary")




def _adapter_argv(
    binary: str,
    parsed: ParsedArgs,
    *,
    include_effort: bool,
    include_watchdog: bool = False,
) -> tuple[list[str], dict[str, str]]:
    argv = [binary, "--name", parsed.agent_name]
    if parsed.team_name is not None:
        argv.extend(["--team", parsed.team_name])
    if parsed.plan_mode_required:
        argv.append("--plan-mode")
    agent_config = _load_agent_config(parsed.team_name, parsed.agent_name)
    if "model" in agent_config:
        argv.extend(["--model", agent_config["model"]])
    if include_effort and "effort" in agent_config:
        argv.extend(["--effort", agent_config["effort"]])
    if "turn_timeout_s" in agent_config:
        argv.extend(["--turn-timeout-s", agent_config["turn_timeout_s"]])
    if include_watchdog and "non_progress_warn_s" in agent_config:
        argv.extend(["--non-progress-warn-s", agent_config["non_progress_warn_s"]])
    if include_watchdog and "non_progress_interrupt_s" in agent_config:
        argv.extend(
            ["--non-progress-interrupt-s", agent_config["non_progress_interrupt_s"]]
        )
    if include_watchdog and "wrapper_tool_failure_window_s" in agent_config:
        argv.extend(
            [
                "--wrapper-tool-failure-window-s",
                agent_config["wrapper_tool_failure_window_s"],
            ]
        )
    return argv, agent_config

def _viewer_disabled() -> bool:
    raw = os.environ.get(VIEWER_ENABLE_ENV, "").strip().lower()
    return raw in {"0", "false", "no", "off"}


def _resolve_viewer_binary() -> str | None:
    """Locate the Ink viewer entrypoint (`viewer/codex-ink-viewer.mjs`).

    Tries an explicit env override first, then walks up from this module to a
    sibling `viewer/` directory (source-checkout layout). Returns None when
    the script can't be found — callers fall back to a plain adapter exec so a
    missing viewer never breaks teammate spawn.
    """
    override = os.environ.get(VIEWER_BINARY_ENV)
    if override and os.path.isfile(override):
        return override

    here = os.path.dirname(os.path.abspath(__file__))
    # here = <repo>/src/claude_anyteam ; repo root is two levels up.
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(here)), "viewer", "codex-ink-viewer.mjs"),
        os.path.join(os.path.dirname(here), "viewer", "codex-ink-viewer.mjs"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def _codex_event_fifo_path(parsed: ParsedArgs) -> str:
    team = parsed.team_name or "default"
    agent = parsed.agent_name or "codex"
    return f"/tmp/claude-anyteam-{team}-{agent}.fifo"


def _exec_codex_with_viewer(adapter_argv: list[str], parsed: ParsedArgs) -> int:
    """Launch the Codex adapter in the background and exec the Ink viewer in
    the foreground so the cmux split shows the fancy live TUI.

    Wiring:
    * Sets `CLAUDE_ANYTEAM_EVENT_FIFO` so the adapter tees raw App Server
      notifications into the pipe. The adapter creates the FIFO node lazily;
      the viewer's read loop retries until it appears, so spawn ordering is
      not load-bearing.
    * Background-spawns the adapter via `subprocess.Popen` (inherits our env,
      including the FIFO path).
    * Foreground-execs `node <viewer> --name <agent> --fifo <path>`, replacing
      this process so the viewer owns the split's TTY.

    Degradation: if the viewer script or `node` can't be found — or the
    adapter fails to spawn — we fall back to a direct `os.execv(adapter)`,
    i.e. exactly the pre-viewer behavior. The teammate must run regardless of
    whether the cosmetic viewer is available.
    """
    binary = adapter_argv[0]

    if _viewer_disabled():
        os.execv(binary, adapter_argv)
        return 0

    viewer_script = _resolve_viewer_binary()
    node_binary = shutil.which("node")
    if viewer_script is None or node_binary is None:
        # Viewer unavailable (no node on PATH, or script not found in this
        # install layout). Fall back to the pre-viewer behavior silently —
        # the `spawn_shim.dispatch` line already recorded the codex route, so
        # we keep stderr to a single structured line for that dispatch.
        os.execv(binary, adapter_argv)
        return 0

    fifo_path = _codex_event_fifo_path(parsed)
    os.environ[EVENT_FIFO_ENV] = fifo_path

    # Create FIFO before spawning adapter or viewer so both can open it
    try:
        os.mkfifo(fifo_path)
    except FileExistsError:
        pass
    except OSError:
        pass

    # Redirect adapter stdout/stderr to log file so the viewer owns the TTY
    adapter_log = f"/tmp/claude-anyteam-{parsed.team_name or 'default'}-{parsed.agent_name or 'codex'}.log"
    try:
        log_fd = open(adapter_log, "a")
        subprocess.Popen(adapter_argv, stdout=log_fd, stderr=log_fd, stdin=subprocess.DEVNULL)
    except OSError as exc:
        # Adapter failed to even start in the background; fall back to a
        # blocking exec so the failure surfaces the same way it used to.
        os.environ.pop(EVENT_FIFO_ENV, None)
        sys.stderr.write(
            json.dumps(
                {
                    "event": "spawn_shim.codex_viewer_adapter_spawn_failed",
                    "agent_name": parsed.agent_name,
                    "error": str(exc),
                },
                sort_keys=True,
            )
            + "\n"
        )
        sys.stderr.flush()
        os.execv(binary, adapter_argv)
        return 0

    viewer_argv = [
        node_binary,
        viewer_script,
        "--name",
        parsed.agent_name or "codex",
        "--fifo",
        fifo_path,
    ]
    _log_dispatch("codex.viewer", parsed.agent_name, viewer_script)
    os.execv(node_binary, viewer_argv)
    return 0


def main(argv: list[str] | None = None) -> int:
    full_argv = [sys.argv[0], *(list(argv) if argv is not None else sys.argv[1:])]
    parsed = _parse_args(full_argv[1:])

    if not parsed.saw_identity_flags:
        # If --print is present but no positional prompt follows, this is a
        # startup probe from Claude Code validating the binary. Exit cleanly
        # so the probe succeeds without spawning a broken claude --print call.
        rest = full_argv[1:]
        has_print = "--print" in rest
        has_prompt = any(not a.startswith("-") for a in rest)
        if has_print and not has_prompt:
            _log_dispatch("probe", None, None)
            return 0
        binary = _require_binary(_resolve_native_claude(full_argv[0]), "claude")
        _log_dispatch("native", parsed.agent_name, binary)
        os.execv(binary, full_argv)
        return 0

    if _codex_route(parsed):
        refusal = _maybe_refuse_bare_routed_prefix("codex", parsed)
        if refusal is not None:
            return refusal
        # Azir fork: unset proxy vars that break Codex API connectivity
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ.pop(var, None)
        binary = _require_binary(
            _resolve_binary(PRIMARY_BINARY, BINARY_ENV, LEGACY_BINARY_ENV, fallback_name=LEGACY_BINARY),
            PRIMARY_BINARY,
        )
        adapter_argv, agent_config = _adapter_argv(
            binary, parsed, include_effort=True, include_watchdog=True
        )
        _log_dispatch("codex", parsed.agent_name, binary, agent_config or None)
        return _exec_codex_with_viewer(adapter_argv, parsed)

    if _gemini_route(parsed):
        refusal = _maybe_refuse_bare_routed_prefix("gemini", parsed)
        if refusal is not None:
            return refusal
        binary = _require_binary(_resolve_binary(GEMINI_BINARY, GEMINI_BINARY_ENV), GEMINI_BINARY)
        adapter_argv, agent_config = _adapter_argv(binary, parsed, include_effort=True)
        _log_dispatch("gemini", parsed.agent_name, binary, agent_config or None)
        os.execv(binary, adapter_argv)
        return 0

    if _kimi_route(parsed):
        refusal = _maybe_refuse_bare_routed_prefix("kimi", parsed)
        if refusal is not None:
            return refusal
        binary = _require_binary(_resolve_binary(KIMI_BINARY, KIMI_BINARY_ENV), KIMI_BINARY)
        adapter_argv, agent_config = _adapter_argv(binary, parsed, include_effort=True)
        _log_dispatch("kimi", parsed.agent_name, binary, agent_config or None)
        os.execv(binary, adapter_argv)
        return 0

    if _antigravity_route(parsed):
        refusal = _maybe_refuse_bare_routed_prefix("antigravity", parsed)
        if refusal is not None:
            return refusal
        binary = _require_binary(
            _resolve_binary(ANTIGRAVITY_BINARY, ANTIGRAVITY_BINARY_ENV),
            ANTIGRAVITY_BINARY,
        )
        adapter_argv, agent_config = _adapter_argv(binary, parsed, include_effort=True)
        _log_dispatch("antigravity", parsed.agent_name, binary, agent_config or None)
        os.execv(binary, adapter_argv)
        return 0

    if _claude_route(parsed):
        binary = _require_binary(_resolve_native_claude(full_argv[0]), "claude")
        _log_dispatch("claude", parsed.agent_name, binary)
        os.execv(binary, full_argv)
        return 0

    binary = _require_binary(_resolve_native_claude(full_argv[0]), "claude")
    _log_dispatch("native", parsed.agent_name, binary)
    os.execv(binary, full_argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
