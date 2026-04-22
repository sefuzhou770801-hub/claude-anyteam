"""Console entry point for the adapter.

Installed as `codex-teammate` via pyproject.toml [project.scripts]. Parses
CLI args, builds Settings, and runs the control loop.
"""

from __future__ import annotations

import argparse
import sys

from . import logger
from .config import from_env
from .loop import run


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="codex-teammate",
        description="OpenAI Codex CLI as a first-class teammate in Claude Code's agent-team protocol.",
    )
    p.add_argument("--team", help="Team name (overrides CODEX_TEAMMATE_TEAM)")
    p.add_argument("--name", help="Teammate name within the team (overrides CODEX_TEAMMATE_NAME)")
    p.add_argument("--cwd", help="Working directory for Codex invocations")
    p.add_argument("--poll-s", type=float, help="Inbox poll interval in seconds")
    p.add_argument("--color", help="Display color (default: cyan)")
    p.add_argument(
        "--plan-mode",
        action="store_true",
        help="Register with planModeRequired=true (opt-in path).",
    )
    p.add_argument("--codex-binary", help="Codex CLI binary name (default: codex)")
    # v7.1 default (task #21): App Server mode is ON unless explicitly
    # disabled via --no-app-server. Rationale: mid-task reactivity is a
    # v7.1 signature capability and users shouldn't need to opt into it;
    # the opt-out exists for v7.2-style session-memory scenarios, since
    # cross-task memory via `codex exec resume` is a documented non-goal
    # under App Server (see docs/v7.2-notes.md §6 and §9).
    #
    # argparse.BooleanOptionalAction auto-generates `--app-server` and
    # `--no-app-server` from a single declaration.
    p.add_argument(
        "--app-server",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Invoke Codex via `codex app-server` (default: on; "
            "pass --no-app-server to use the legacy `codex exec` path "
            "with v7.2 session-memory support)."
        ),
    )
    p.add_argument(
        "--model",
        help=(
            "Codex model slug (e.g. gpt-5.4, gpt-5.3-codex). "
            "Overrides CODEX_TEAMMATE_MODEL; when unset, Codex's "
            "~/.codex/config.toml default applies."
        ),
    )
    p.add_argument(
        "--effort",
        choices=["low", "medium", "high", "xhigh"],
        help=(
            "Reasoning effort for Codex. Overrides CODEX_TEAMMATE_EFFORT; "
            "when unset, Codex's per-model default applies."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    overrides: dict[str, object] = {}
    if args.team:
        overrides["team_name"] = args.team
    if args.name:
        overrides["agent_name"] = args.name
    if args.cwd:
        overrides["cwd"] = args.cwd
    if args.poll_s is not None:
        overrides["poll_interval_s"] = args.poll_s
    if args.color:
        overrides["color"] = args.color
    if args.plan_mode:
        overrides["plan_mode_required"] = "true"
    if args.codex_binary:
        overrides["codex_binary"] = args.codex_binary
    # args.app_server is None when the flag isn't passed → fall through to
    # env/default. Only override when the user explicitly said --app-server
    # or --no-app-server on the command line.
    if args.app_server is not None:
        overrides["app_server"] = "true" if args.app_server else "false"
    if args.model:
        overrides["model"] = args.model
    if args.effort:
        overrides["effort"] = args.effort

    try:
        settings = from_env(overrides=overrides)
    except ValueError as e:
        logger.error("startup.config_error", error=str(e))
        return 2

    logger.info(
        "startup",
        team=settings.team_name,
        name=settings.agent_name,
        cwd=str(settings.cwd),
        plan_mode=settings.plan_mode_required,
        app_server=settings.app_server,
        model=settings.model,
        effort=settings.effort,
    )
    return run(settings)


if __name__ == "__main__":
    sys.exit(main())
