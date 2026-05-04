"""Regression tests for the codex adapter's startup-crash visibility path.

Issue #32 — WSL2 Codex teammate spawn writes ``tmuxPaneId`` to team config
but no codex process exists / no registration ever fires. Reporter
root-caused this to ``httpx==1.0.dev3`` being promoted into the tool venv,
which broke ``from fastmcp import FastMCP`` at import time. The codex
spawn-shim then crashed inside its tmux pane and the wrapper's
``_probe_wrapper_mcp`` (and therefore ``feature_test``) raised a
``RuntimeError`` *before* the codex adapter's main loop and registration
ran. Result: the lead saw a teammate that never registered, with no
telemetry to distinguish "still booting" from "dead at bootstrap."

The fix mirrors what the gemini and kimi backends already do (see
``test_startup_visibility.py``): wrap bootstrap in a try/except that fans
out a structured ``visibility_degraded`` envelope to the lead mailbox +
event log AND records a diagnostic incident. The test below pins that
behavior so a future refactor cannot silently revert to the
silent-spawn-failure mode in #32.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]

from claude_anyteam import codex as codex_mod
from claude_anyteam import diagnostics
from claude_anyteam import loop as codex_loop
from claude_anyteam import protocol_io as pio
from claude_anyteam.config import Settings


@pytest.fixture
def events_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "home" / ".claude" / "teams"
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", base)
    return base


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        team_name="team-codex",
        agent_name="codex-alice",
        cwd=tmp_path,
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex-broken",
    )


def _lead_visibility_messages(root: Path, *, team: str = "team-codex") -> list[dict[str, Any]]:
    path = root / team / "inboxes" / "team-lead.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    return [
        json.loads(message["text"])
        for message in raw
        if message.get("messageKind") == "visibility_degraded"
    ]


def test_codex_feature_test_failure_fans_out_visibility_degraded(
    events_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``feature_test`` raising must surface to the lead, not exit silently.

    Models the #32 root cause: the wrapper-MCP probe inside ``feature_test``
    hits an ``ImportError``-equivalent (``from fastmcp import FastMCP``
    failing because ``httpx==1.0.dev3`` was promoted into the venv) and
    raises ``RuntimeError("wrapper build_server() probe failed: ...")``
    before ``register()`` runs. The lead must see a ``visibility_degraded``
    envelope (event log + mailbox) and a ``record_incident`` diagnostic.
    """

    incidents: list[dict[str, Any]] = []
    monkeypatch.setattr(
        diagnostics,
        "record_incident",
        lambda **kwargs: (incidents.append(kwargs) or "inc-test"),
    )

    def crash(
        binary: str,
        *,
        mcp_probe: bool = False,
        team: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        # The wire shape codex.py:_probe_wrapper_mcp would produce in
        # the httpx-version-mismatch scenario.
        raise RuntimeError(
            f"wrapper build_server() probe failed: rc=1 "
            f"stderr='AttributeError: module \\'httpx\\' has no attribute "
            f"\\'TransportError\\''"
        )

    monkeypatch.setattr(codex_mod, "feature_test", crash)

    # ``register`` should never get called if feature_test fails; if it
    # does, we want the test to fail loudly rather than silently invoke
    # the real config path.
    register_calls: list[Any] = []
    monkeypatch.setattr(
        codex_loop,
        "register",
        lambda *args, **kwargs: register_calls.append((args, kwargs)),
    )

    assert codex_loop.run(_settings(tmp_path)) == 1
    assert register_calls == []

    events = pio.read_events("team-codex", "codex-alice")
    assert len(events) == 1
    event = events[0]
    assert event.kind == "visibility_degraded"
    assert event.severity == "error"
    assert event.visibility.mailbox is True
    assert event.visibility.event_log is True
    assert event.payload["surface"] == "adapter_startup"
    assert event.payload["phase"] == "feature_test"
    assert event.payload["codex_binary"] == "codex-broken"
    raw = event.payload["raw_backend_error"]
    assert raw["type"] == "builtins.RuntimeError"
    assert "wrapper build_server() probe failed" in raw["message"]

    lead_events = _lead_visibility_messages(events_root)
    assert len(lead_events) == 1
    assert lead_events[0]["event_id"] == event.event_id

    assert len(incidents) == 1
    incident = incidents[0]
    assert incident["team"] == "team-codex"
    assert incident["agent"] == "codex-alice"
    assert incident["backend"] == "codex"
    assert incident["error_class"] == "adapter_startup_crash"


def test_codex_register_failure_fans_out_visibility_degraded(
    events_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registration failure (e.g. team config locked / unreadable)
    must also surface to the lead — feature_test passes but register
    raises, and the same visibility-parity contract applies.
    """

    incidents: list[dict[str, Any]] = []
    monkeypatch.setattr(
        diagnostics,
        "record_incident",
        lambda **kwargs: (incidents.append(kwargs) or "inc-test"),
    )
    monkeypatch.setattr(codex_mod, "feature_test", lambda *_a, **_kw: None)

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("team config not found at /nope/config.json")

    monkeypatch.setattr(codex_loop, "register", boom)

    assert codex_loop.run(_settings(tmp_path)) == 1

    events = pio.read_events("team-codex", "codex-alice")
    assert len(events) == 1
    event = events[0]
    assert event.kind == "visibility_degraded"
    assert event.payload["surface"] == "adapter_startup"
    assert event.payload["phase"] == "registration"

    lead_events = _lead_visibility_messages(events_root)
    assert len(lead_events) == 1
    assert lead_events[0]["payload"]["phase"] == "registration"
    assert len(incidents) == 1
    assert incidents[0]["error_class"] == "adapter_startup_crash"
