"""Runtime configuration for the adapter.

Resolved from (in order of precedence): CLI flags > environment variables >
defaults. Produced once at startup; the rest of the adapter treats it as
immutable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    team_name: str
    agent_name: str
    cwd: Path
    poll_interval_s: float
    color: str
    plan_mode_required: bool
    codex_binary: str
    # v7.1: when True, invoke Codex via `codex app-server` with a long-lived
    # JSON-RPC session so the adapter can inject mid-task turns via
    # `turn/steer`. Default was False in v7.1 (opt-in); flipped to True
    # in task #21 per user direction — mid-task reactivity is a default-
    # capability rather than a flag that has to be discovered. Users
    # wanting v7.2 `codex exec resume` session memory must opt out via
    # `--no-app-server` (the two modes are orthogonal; see
    # docs/v7.2-notes.md §6).
    app_server: bool = True
    # Codex model slug (e.g. "gpt-5.4", "gpt-5.3-codex") and reasoning effort
    # (low|medium|high|xhigh). Both optional: when unset the adapter passes
    # no override and Codex falls back to `~/.codex/config.toml` defaults,
    # preserving the pre-v7.3 behavior. When set they flow through both the
    # App Server path (as `model`/`effort` JSON-RPC params on thread/start
    # and turn/start) and the fresh-exec path (as `-c model="…"` /
    # `-c model_reasoning_effort="…"` CLI overrides). Per-teammate tuning:
    # different adapter processes on the same host can run at different
    # model/effort without any global config surgery.
    model: str | None = None
    effort: str | None = None


def from_env(overrides: dict[str, object] | None = None) -> Settings:
    overrides = overrides or {}

    team_name = _pick(overrides, "team_name", os.environ.get("CODEX_TEAMMATE_TEAM"))
    agent_name = _pick(overrides, "agent_name", os.environ.get("CODEX_TEAMMATE_NAME"))
    if not team_name:
        raise ValueError("team_name is required (CLI --team or CODEX_TEAMMATE_TEAM)")
    if not agent_name:
        raise ValueError("agent_name is required (CLI --name or CODEX_TEAMMATE_NAME)")

    cwd_raw = _pick(overrides, "cwd", os.environ.get("CODEX_TEAMMATE_CWD", os.getcwd()))
    cwd = Path(str(cwd_raw)).resolve()
    if not cwd.is_absolute():
        raise ValueError(f"cwd must be absolute, got {cwd}")

    poll = float(_pick(overrides, "poll_interval_s", os.environ.get("CODEX_TEAMMATE_POLL_S", "1.5")))
    color = str(_pick(overrides, "color", os.environ.get("CODEX_TEAMMATE_COLOR", "cyan")))

    plan_raw = str(_pick(overrides, "plan_mode_required", os.environ.get("CODEX_TEAMMATE_PLAN_MODE", "false")))
    plan_mode_required = plan_raw.lower() in {"1", "true", "yes", "on"}

    codex_binary = str(_pick(overrides, "codex_binary", os.environ.get("CODEX_BINARY", "codex")))

    # v7.1 default (task #21): App Server is ON unless explicitly disabled
    # via --no-app-server or CODEX_TEAMMATE_APP_SERVER=false. Users who
    # want the v7.2 `codex exec resume` session-memory path must opt out
    # explicitly (the two modes are orthogonal; see docs/v7.2-notes.md §6).
    app_server_raw = str(
        _pick(overrides, "app_server", os.environ.get("CODEX_TEAMMATE_APP_SERVER", "true"))
    )
    app_server = app_server_raw.lower() in {"1", "true", "yes", "on"}

    model_raw = _pick(overrides, "model", os.environ.get("CODEX_TEAMMATE_MODEL"))
    model = str(model_raw) if model_raw else None

    effort_raw = _pick(overrides, "effort", os.environ.get("CODEX_TEAMMATE_EFFORT"))
    effort = str(effort_raw) if effort_raw else None
    if effort is not None and effort not in {"low", "medium", "high", "xhigh"}:
        raise ValueError(
            f"effort must be one of low|medium|high|xhigh, got {effort!r}"
        )

    return Settings(
        team_name=str(team_name),
        agent_name=str(agent_name),
        cwd=cwd,
        poll_interval_s=poll,
        color=color,
        plan_mode_required=plan_mode_required,
        codex_binary=codex_binary,
        app_server=app_server,
        model=model,
        effort=effort,
    )


def _pick(overrides: dict[str, object], key: str, fallback: object | None) -> object | None:
    if key in overrides and overrides[key] is not None:
        return overrides[key]
    return fallback
