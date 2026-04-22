"""Structured stderr logger.

Emits one JSON object per log line to stderr so output is trivially greppable.
Honours CODEX_TEAMMATE_LOG env var; defaults to INFO.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}


def _threshold() -> int:
    raw = os.environ.get("CODEX_TEAMMATE_LOG", "info").lower()
    return _LEVELS.get(raw, _LEVELS["info"])


def _emit(level: str, msg: str, fields: dict[str, Any] | None) -> None:
    if _LEVELS[level] < _threshold():
        return
    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": level,
        "msg": msg,
    }
    if fields:
        payload.update(fields)
    sys.stderr.write(json.dumps(payload, default=str) + "\n")
    sys.stderr.flush()


def debug(msg: str, **fields: Any) -> None:
    _emit("debug", msg, fields or None)


def info(msg: str, **fields: Any) -> None:
    _emit("info", msg, fields or None)


def warn(msg: str, **fields: Any) -> None:
    _emit("warn", msg, fields or None)


def error(msg: str, **fields: Any) -> None:
    _emit("error", msg, fields or None)
