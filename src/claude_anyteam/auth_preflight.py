"""Shared auth pre-flight helpers for routed CLI backends."""
from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from typing import Literal

AuthErrorClass = Literal["quota_exhausted", "invalid_authentication"]


class AuthPreflightFailure(RuntimeError):
    """Raised when a cheap spawn-time auth probe receives an API auth error."""

    def __init__(
        self,
        *,
        backend: str,
        error_class: AuthErrorClass,
        error_message: str,
        reset_after_seconds: int | None = None,
        cmd: Sequence[str] | None = None,
        returncode: int | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> None:
        super().__init__(error_message)
        self.backend = backend
        self.error_class = error_class
        self.error_message = error_message
        self.reset_after_seconds = reset_after_seconds
        self.cmd = list(cmd) if cmd is not None else None
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def subprocess_diagnostic(
    args: Sequence[str],
    *,
    returncode: int | None,
    stdout: str | None,
    stderr: str | None,
) -> str:
    """Return a compact but complete diagnostic for a preflight process."""

    parts = [f"command: {shlex.join([str(a) for a in args])}"]
    if returncode is not None:
        parts.append(f"exit_code: {returncode}")
    if stdout:
        parts.append("stdout:\n" + stdout.rstrip())
    if stderr:
        parts.append("stderr:\n" + stderr.rstrip())
    return "\n".join(parts)


_RESET_DURATION_RE = re.compile(
    r"(?:reset|retry|try again)(?:s)?\s+(?:after|in)\s+((?:\d+\s*[dhms]\s*)+)",
    re.IGNORECASE,
)
_RETRY_AFTER_RE = re.compile(r"retry-after\s*[:=]\s*(\d+)", re.IGNORECASE)
_UNIT_RE = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)


def reset_after_seconds(text: str) -> int | None:
    """Extract a reset/retry duration from common CLI/API diagnostics."""

    retry_after = _RETRY_AFTER_RE.search(text)
    if retry_after:
        return int(retry_after.group(1))
    match = _RESET_DURATION_RE.search(text)
    if not match:
        return None
    total = 0
    for amount, unit in _UNIT_RE.findall(match.group(1)):
        value = int(amount)
        unit = unit.lower()
        if unit == "d":
            total += value * 86400
        elif unit == "h":
            total += value * 3600
        elif unit == "m":
            total += value * 60
        elif unit == "s":
            total += value
    return total or None


def classify_auth_error(text: str) -> tuple[AuthErrorClass, int | None] | None:
    """Classify backend API diagnostics that should fail spawn fast."""

    lowered = text.lower()
    quota_markers = (
        "quota_exhausted",
        "resource_exhausted",
        "quota exhausted",
        "quota has been exceeded",
        "exceeded your quota",
        "exhausted your capacity",
        "capacity on this model",
        "rate limit exceeded",
        "rate_limit_exceeded",
        "too many requests",
        "429",
    )
    if any(marker in lowered for marker in quota_markers):
        return "quota_exhausted", reset_after_seconds(text)

    invalid_markers = (
        "invalid_authentication",
        "invalid authentication",
        "invalid api key",
        "api key not valid",
        "incorrect api key",
        "unauthorized",
        "unauthenticated",
        "not authenticated",
        "authentication required",
        "authentication failed",
        "auth failed",
        "login required",
        "credentials not found",
        "no credentials",
        "permission denied",
        "moonshot_api_key",
        "401",
    )
    if any(marker in lowered for marker in invalid_markers):
        return "invalid_authentication", None
    return None
