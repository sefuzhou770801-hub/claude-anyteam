"""Claude teammate spawn shim.

This module is intentionally stdlib-only so it can be imported standalone.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass

DEFAULT_MATCH = r"^codex-"


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


def _resolve_binary(name: str, env_var: str) -> str | None:
    override = os.environ.get(env_var)
    if override:
        return shutil.which(override) or override
    return shutil.which(name)


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
    override = os.environ.get("CODEX_TEAMMATE_NATIVE_CLAUDE")
    if override:
        return shutil.which(override) or override

    current = _resolve_current_invocation(argv0)
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


def _codex_route(parsed: ParsedArgs) -> bool:
    if not parsed.agent_name:
        return False
    pattern = os.environ.get("CODEX_TEAMMATE_SHIM_MATCH", DEFAULT_MATCH)
    try:
        return re.search(pattern, parsed.agent_name) is not None
    except re.error as exc:
        raise SystemExit(
            f"Invalid CODEX_TEAMMATE_SHIM_MATCH regex: {pattern!r}: {exc}"
        ) from exc


def _log_dispatch(route: str, agent_name: str | None, binary: str | None) -> None:
    record = {
        "event": "spawn_shim.dispatch",
        "route": route,
        "agent_name": agent_name,
        "binary": binary,
    }
    sys.stderr.write(json.dumps(record, sort_keys=True) + "\n")
    sys.stderr.flush()


def _require_binary(binary: str | None, label: str) -> str:
    if binary:
        return binary
    raise SystemExit(f"Unable to resolve {label} binary")


def main(argv: list[str] | None = None) -> int:
    full_argv = [sys.argv[0], *(list(argv) if argv is not None else sys.argv[1:])]
    parsed = _parse_args(full_argv[1:])

    if not parsed.saw_identity_flags:
        binary = _require_binary(_resolve_native_claude(full_argv[0]), "claude")
        _log_dispatch("native", parsed.agent_name, binary)
        os.execv(binary, full_argv)
        return 0

    if _codex_route(parsed):
        binary = _require_binary(
            _resolve_binary("codex-teammate", "CODEX_TEAMMATE_BINARY"),
            "codex-teammate",
        )
        codex_argv = [binary, "--name", parsed.agent_name]
        if parsed.team_name is not None:
            codex_argv.extend(["--team", parsed.team_name])
        if parsed.plan_mode_required:
            codex_argv.append("--plan-mode")
        _log_dispatch("codex", parsed.agent_name, binary)
        os.execv(binary, codex_argv)
        return 0

    binary = _require_binary(_resolve_native_claude(full_argv[0]), "claude")
    _log_dispatch("native", parsed.agent_name, binary)
    os.execv(binary, full_argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
