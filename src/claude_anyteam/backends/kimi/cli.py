"""Console entry point for Kimi-backed teammates."""
from __future__ import annotations

import argparse

from .config import KIMI_EFFORTS, from_env
from .loop import run


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kimi-anyteam", description="Route kimi-* teammates through Kimi CLI.")
    p.add_argument("--team", help="Team name (overrides CLAUDE_ANYTEAM_TEAM)")
    p.add_argument("--name", help="Teammate name within the team (overrides CLAUDE_ANYTEAM_NAME)")
    p.add_argument("--cwd", help="Working directory for Kimi invocations")
    p.add_argument("--poll-s", type=float, help="Inbox poll interval in seconds")
    p.add_argument("--color", help="Display color (default: cyan)")
    p.add_argument("--plan-mode", action="store_true", help="Register with planModeRequired=true")
    p.add_argument("--kimi-binary", help="Kimi CLI binary name (default: kimi)")
    p.add_argument("--model", help="Kimi model slug passed as --model. Overrides CLAUDE_ANYTEAM_MODEL.")
    p.add_argument("--effort", choices=sorted(KIMI_EFFORTS), help="Kimi thinking effort tier. Overrides CLAUDE_ANYTEAM_KIMI_EFFORT.")
    p.add_argument("--kimi-home", help="Adapter-owned HOME root for Kimi config/session state")
    p.add_argument("--backend", choices=("headless", "acp"), default="headless", help="Kimi backend transport (default: headless)")
    p.add_argument("--thinking", choices=("on", "off", "auto"), default="auto", help="Kimi thinking mode (default: auto)")
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
        "kimi_binary": ns.kimi_binary,
        "model": ns.model,
        "effort": ns.effort,
        "kimi_home": ns.kimi_home,
        "backend": ns.backend,
        "thinking": ns.thinking,
    })
    return run(settings)


if __name__ == "__main__":
    raise SystemExit(main())
