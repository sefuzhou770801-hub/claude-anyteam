"""Best-effort diagnostics for wrapper MCP tool discovery.

The SendMessage flap is specifically about whether Codex saw the wrapper
MCP's tools.  Normal visibility events only exist *after* a tool is called,
so they cannot distinguish "Codex never listed the wrapper" from "Codex
listed it and hallucinated anyway".  This module writes a low-level JSONL
trace beside the team state.  It must never make the wrapper or adapter fail.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def diagnostics_log_path(team: str, *, root: Path | None = None) -> Path:
    teams_root = root or (Path.home() / ".claude" / "teams")
    return teams_root / team / "diagnostics" / "wrapper-mcp-tools.jsonl"


def _jsonable(value: Any, *, limit: int = 2000) -> Any:
    """Return a bounded JSON-serializable value for diagnostics payloads."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[: limit - 1] + "…"
    if isinstance(value, (list, tuple)):
        return [_jsonable(item, limit=limit) for item in list(value)[:50]]
    if isinstance(value, dict):
        return {
            str(k): _jsonable(v, limit=limit)
            for k, v in list(value.items())[:80]
        }
    return _jsonable(repr(value), limit=limit)


def append_wrapper_mcp_diagnostic(
    *,
    team: str,
    agent: str,
    event: str,
    payload: dict[str, Any] | None = None,
    root: Path | None = None,
) -> None:
    """Append one diagnostic row, swallowing all errors.

    Multiple wrapper subprocesses may append to the same per-team file.  Rows
    are intentionally small; append-mode writes are sufficient for this
    observational trace and keep the hot path dependency-free.
    """

    try:
        path = diagnostics_log_path(team, root=root)
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "schema_version": 1,
            "timestamp": _now_iso(),
            "team": team,
            "agent": agent,
            "pid": os.getpid(),
            "event": event,
            "payload": _jsonable(payload or {}),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    except Exception:
        # Diagnostics must never perturb the MCP handshake or a tool call.
        return
