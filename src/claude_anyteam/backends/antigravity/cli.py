"""Console entry point for Antigravity (agy) -backed teammates."""
from __future__ import annotations

import argparse

from .config import ANTIGRAVITY_BACKENDS, ANTIGRAVITY_EFFORTS, from_env
from .loop import run


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="antigravity-anyteam",
        description="Route antigravity-* teammates through Antigravity (agy) CLI.",
    )
    p.add_argument("--team", help="Team name (overrides CLAUDE_ANYTEAM_TEAM)")
    p.add_argument(
        "--name",
        help="Teammate name within the team (overrides CLAUDE_ANYTEAM_NAME)",
    )
    p.add_argument("--cwd", help="Working directory for Antigravity invocations")
    p.add_argument("--poll-s", type=float, help="Inbox poll interval in seconds")
    p.add_argument("--color", help="Display color (default: cyan)")
    p.add_argument(
        "--plan-mode",
        action="store_true",
        help="Register with planModeRequired=true",
    )
    p.add_argument(
        "--antigravity-binary",
        help="Antigravity CLI binary name (default: agy)",
    )
    p.add_argument(
        "--model",
        help=(
            "Model slug echoed into telemetry (agy ignores this today). "
            "Overrides CLAUDE_ANYTEAM_MODEL."
        ),
    )
    p.add_argument(
        "--effort",
        choices=sorted(ANTIGRAVITY_EFFORTS),
        help=(
            "Effort tier echoed into telemetry (agy has no native effort flag "
            "today). Overrides CLAUDE_ANYTEAM_EFFORT."
        ),
    )
    p.add_argument(
        "--antigravity-home",
        help="Adapter-owned HOME root for Antigravity state",
    )
    p.add_argument(
        "--backend",
        choices=sorted(ANTIGRAVITY_BACKENDS),
        default="headless",
        help="Antigravity backend transport (default: headless)",
    )
    p.add_argument(
        "--sandbox",
        action="store_true",
        help="Pass --sandbox to agy (terminal-restricted sandbox)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    ns = _build_parser().parse_args(argv)
    settings = from_env(
        {
            "team_name": ns.team,
            "agent_name": ns.name,
            "cwd": ns.cwd,
            "poll_interval_s": ns.poll_s,
            "color": ns.color,
            "plan_mode_required": True if ns.plan_mode else None,
            "antigravity_binary": ns.antigravity_binary,
            "model": ns.model,
            "effort": ns.effort,
            "antigravity_home": ns.antigravity_home,
            "backend": ns.backend,
            "sandbox": True if ns.sandbox else None,
        }
    )
    return run(settings)


if __name__ == "__main__":
    raise SystemExit(main())
