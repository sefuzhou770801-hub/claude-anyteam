"""Runtime configuration for the Antigravity-backed claude-anyteam adapter.

Antigravity (``agy``) is a Go CLI that runs headless prompts via
``agy --print -p "<prompt>"``.  It does not (yet) expose an MCP-config
flag, a stream-json output mode, or a ``--thinking`` knob.  This config
intentionally stays minimal to match that surface: anything the
binary does not accept is dropped at the invocation layer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from claude_anyteam.config import _pick
from claude_anyteam.env import (
    ANTIGRAVITY_BINARY_ENV,
    COLOR_ENV,
    CWD_ENV,
    EFFORT_ENV,
    MODEL_ENV,
    NAME_ENV,
    PLAN_MODE_ENV,
    POLL_ENV,
    TEAM_ENV,
    env_first,
)

ANTIGRAVITY_HOME_ENV = "CLAUDE_ANYTEAM_ANTIGRAVITY_HOME"
ANTIGRAVITY_BACKEND_ENV = "CLAUDE_ANYTEAM_ANTIGRAVITY_BACKEND"
ANTIGRAVITY_SANDBOX_ENV = "CLAUDE_ANYTEAM_ANTIGRAVITY_SANDBOX"

# Matches Kimi's effort-tier vocabulary for symmetry with the team-agent
# config writer; agy itself has no effort flag today so the value is only
# echoed back into prompt context / telemetry.
ANTIGRAVITY_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh"})
ANTIGRAVITY_BACKENDS = frozenset({"headless"})


@dataclass(frozen=True)
class AntigravitySettings:
    team_name: str
    agent_name: str
    cwd: Path
    poll_interval_s: float
    color: str
    plan_mode_required: bool
    antigravity_binary: str = "agy"
    model: str | None = None
    effort: str | None = None
    antigravity_home: Path | None = None
    backend: Literal["headless"] = "headless"
    sandbox: bool = False


def from_env(overrides: dict[str, object] | None = None) -> AntigravitySettings:
    overrides = overrides or {}
    team_name = _pick(overrides, "team_name", env_first(os.environ, TEAM_ENV))
    agent_name = _pick(overrides, "agent_name", env_first(os.environ, NAME_ENV))
    if not team_name:
        raise ValueError(f"team_name is required (CLI --team or {TEAM_ENV})")
    if not agent_name:
        raise ValueError(f"agent_name is required (CLI --name or {NAME_ENV})")

    cwd_raw = _pick(overrides, "cwd", env_first(os.environ, CWD_ENV, default=os.getcwd()))
    cwd = Path(str(cwd_raw)).resolve()
    if not cwd.is_absolute():
        raise ValueError(f"cwd must be absolute, got {cwd}")

    poll = float(_pick(overrides, "poll_interval_s", env_first(os.environ, POLL_ENV, default="1.5")))
    color = str(_pick(overrides, "color", env_first(os.environ, COLOR_ENV, default="cyan")))
    plan_raw = str(_pick(overrides, "plan_mode_required", env_first(os.environ, PLAN_MODE_ENV, default="false")))
    plan_mode_required = plan_raw.lower() in {"1", "true", "yes", "on"}

    model_raw = _pick(overrides, "model", env_first(os.environ, MODEL_ENV))
    effort_raw = _pick(overrides, "effort", env_first(os.environ, EFFORT_ENV))
    effort = str(effort_raw) if effort_raw else None
    if effort is not None and effort not in ANTIGRAVITY_EFFORTS:
        raise ValueError(
            f"Antigravity effort must be one of minimal|low|medium|high|xhigh, got {effort!r}"
        )

    home_raw = _pick(overrides, "antigravity_home", os.environ.get(ANTIGRAVITY_HOME_ENV))
    backend_raw = str(_pick(overrides, "backend", os.environ.get(ANTIGRAVITY_BACKEND_ENV, "headless")))
    if backend_raw not in ANTIGRAVITY_BACKENDS:
        raise ValueError(
            f"Antigravity backend must be one of {', '.join(sorted(ANTIGRAVITY_BACKENDS))}, got {backend_raw!r}"
        )

    sandbox_raw = str(
        _pick(overrides, "sandbox", os.environ.get(ANTIGRAVITY_SANDBOX_ENV, "false"))
    )
    sandbox = sandbox_raw.lower() in {"1", "true", "yes", "on"}

    return AntigravitySettings(
        team_name=str(team_name),
        agent_name=str(agent_name),
        cwd=cwd,
        poll_interval_s=poll,
        color=color,
        plan_mode_required=plan_mode_required,
        antigravity_binary=str(
            _pick(overrides, "antigravity_binary", os.environ.get(ANTIGRAVITY_BINARY_ENV, "agy"))
        ),
        model=str(model_raw) if model_raw else None,
        effort=effort,
        antigravity_home=Path(str(home_raw)).expanduser().resolve() if home_raw else None,
        backend=backend_raw,  # type: ignore[arg-type]
        sandbox=sandbox,
    )
