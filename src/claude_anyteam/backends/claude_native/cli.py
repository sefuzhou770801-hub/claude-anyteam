"""Console entry point for native Claude Code headless teammates."""
from __future__ import annotations

import argparse

from .config import CLAUDE_EFFORTS, from_env
from .loop import run


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-native-anyteam",
        description="Run a native Claude Code teammate through the headless Claude CLI.",
    )
    p.add_argument("--team", help="Team name (overrides CLAUDE_ANYTEAM_TEAM)")
    p.add_argument("--name", help="Teammate name within the team (overrides CLAUDE_ANYTEAM_NAME)")
    p.add_argument("--cwd", help="Working directory for Claude invocations")
    p.add_argument("--poll-s", type=float, help="Inbox poll interval in seconds")
    p.add_argument("--color", help="Display color (default: cyan)")
    p.add_argument("--plan-mode", action="store_true", help="Register with planModeRequired=true")
    p.add_argument("--claude-binary", help="Claude Code CLI binary name/path (default: claude)")
    p.add_argument("--model", help="Claude model slug passed as --model")
    p.add_argument("--effort", choices=sorted(CLAUDE_EFFORTS), help="Claude effort tier")
    p.add_argument("--turn-timeout-s", type=float, help="Per-turn Claude CLI timeout in seconds")
    return p


def main(argv: list[str] | None = None) -> int:
    ns = _build_parser().parse_args(argv)
    settings = from_env({
        "team_name": ns.team,
        "agent_name": ns.name,
        "cwd": ns.cwd,
        "poll_interval_s": ns.poll_s,
        "color": ns.color,
        "plan_mode_required": True if ns.plan_mode else None,
        "claude_binary": ns.claude_binary,
        "model": ns.model,
        "effort": ns.effort,
        "turn_timeout_s": ns.turn_timeout_s,
    })
    return run(settings)


if __name__ == "__main__":
    raise SystemExit(main())
