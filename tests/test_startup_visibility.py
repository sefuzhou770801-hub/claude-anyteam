from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]

from claude_anyteam import diagnostics
from claude_anyteam import protocol_io as pio
from claude_anyteam.backends.gemini import loop as gemini_loop
from claude_anyteam.backends.gemini.config import GeminiSettings
from claude_anyteam.backends.kimi import loop as kimi_loop
from claude_anyteam.backends.kimi.config import KimiSettings


@pytest.fixture
def events_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "home" / ".claude" / "teams"
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", base)
    monkeypatch.setattr(diagnostics, "record_incident", lambda **_kwargs: "inc-test")
    return base


def _kimi_settings(tmp_path: Path) -> KimiSettings:
    return KimiSettings(
        team_name="team-x",
        agent_name="kimi-a",
        cwd=tmp_path,
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        kimi_binary="kimi-broken",
        kimi_home=tmp_path / "kimi-home",
    )


def _gemini_settings(tmp_path: Path) -> GeminiSettings:
    return GeminiSettings(
        team_name="team-x",
        agent_name="gemini-a",
        cwd=tmp_path,
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        gemini_binary="gemini-broken",
        gemini_home=tmp_path / "gemini-home",
        backend="acp",
    )


def _raise_wrapped_probe_failure(binary: str, stderr: str) -> None:
    try:
        raise subprocess.CalledProcessError(
            127,
            [binary, "info"],
            output="native stdout",
            stderr=stderr,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"could not probe backend CLI {binary!r}: {exc}") from exc


def _lead_visibility_messages(root: Path, *, team: str = "team-x") -> list[dict[str, Any]]:
    raw = json.loads((root / team / "inboxes" / "team-lead.json").read_text())
    return [
        json.loads(message["text"])
        for message in raw
        if message.get("messageKind") == "visibility_degraded"
    ]


def test_kimi_feature_test_startup_crash_fans_out_visibility_degraded(
    events_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def crash(_settings: KimiSettings) -> None:
        _raise_wrapped_probe_failure("kimi-broken", "kimi binary probe exploded")

    monkeypatch.setattr(kimi_loop, "_backend_feature_test", crash)

    assert kimi_loop.run(_kimi_settings(tmp_path)) == 1

    events = pio.read_events("team-x", "kimi-a")
    assert len(events) == 1
    event = events[0]
    assert event.kind == "visibility_degraded"
    assert event.severity == "error"
    assert event.visibility.mailbox is True
    assert event.payload["surface"] == "adapter_startup"
    assert event.payload["phase"] == "feature_test"
    assert event.payload["kimi_binary"] == "kimi-broken"
    raw = event.payload["raw_backend_error"]
    assert raw["type"] == "builtins.RuntimeError"
    assert "could not probe backend CLI 'kimi-broken'" in raw["message"]
    assert raw["cause"]["type"] == "subprocess.CalledProcessError"
    assert raw["cause"]["cmd"] == ["kimi-broken", "info"]
    assert raw["cause"]["returncode"] == 127
    assert raw["cause"]["stderr"] == "kimi binary probe exploded"

    lead_events = _lead_visibility_messages(events_root)
    assert len(lead_events) == 1
    assert lead_events[0]["event_id"] == event.event_id
    assert lead_events[0]["payload"]["raw_backend_error"] == raw


def test_gemini_feature_test_startup_crash_fans_out_visibility_degraded(
    events_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def crash(_settings: GeminiSettings) -> None:
        raise RuntimeError(
            "Gemini CLI is missing required ACP flag --acp / --experimental-acp "
            "(version '0.0')"
        )

    monkeypatch.setattr(gemini_loop, "_backend_feature_test", crash)

    assert gemini_loop.run(_gemini_settings(tmp_path)) == 1

    events = pio.read_events("team-x", "gemini-a")
    assert len(events) == 1
    event = events[0]
    assert event.kind == "visibility_degraded"
    assert event.backend == "gemini"
    assert event.payload["surface"] == "adapter_startup"
    assert event.payload["phase"] == "feature_test"
    assert event.payload["transport"] == "acp"
    assert event.payload["gemini_binary"] == "gemini-broken"
    assert "--experimental-acp" in event.payload["raw_backend_error"]["message"]

    lead_events = _lead_visibility_messages(events_root)
    assert len(lead_events) == 1
    assert lead_events[0]["kind"] == "visibility_degraded"
    assert lead_events[0]["payload"]["raw_backend_error"] == event.payload["raw_backend_error"]
