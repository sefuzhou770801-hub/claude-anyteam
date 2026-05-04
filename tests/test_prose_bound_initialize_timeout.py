"""Regression tests for #40 Phase 1 — Gap 2 (prose-bound initialize
timeout typed visibility_degraded fan-out).

Until this fix, when a prose-bound turn (e.g. shutdown handshake, peer
DM) hit the App Server initialize timeout, the only signal the lead
received was an apologetic prose_reply: "I received your message but
couldn't generate a reply." That's the §2 violation product-steward
flagged in their pushback: collapse-to-prose instead of typed event.

Per Gap 2 in the steward's brief:

> Use Option B (visibility_degraded with surface tag) for the
> prose-bound timeout path. Ship in this PR. The existing prose_reply
> STAYS as the courtesy response (the shutdown_request handshake needs
> SOME prose response to complete) — but rewrite it to be CONCISE and
> reference the incident_id.

These tests pin both halves: (a) typed event lands with the right
discriminators (`surface`, `phase`, `shutdown_request`); (b) prose
response is concise and points at `claude-anyteam diagnose`.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]

from claude_anyteam import diagnostics
from claude_anyteam import loop as codex_loop
from claude_anyteam import protocol_io as pio
from claude_anyteam.config import Settings


@pytest.fixture
def events_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "home" / ".claude" / "teams"
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", base)
    monkeypatch.setattr(pio._m, "TEAMS_DIR", base)
    monkeypatch.setattr(
        diagnostics, "_diagnostics_dir",
        lambda team, agent: base / team / "diagnostics" / agent,
    )
    return base


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        team_name="team-codex",
        agent_name="codex-alice",
        cwd=tmp_path,
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
    )


def _initialize_timeout_result() -> SimpleNamespace:
    """The shape ``app_server_invoke()`` returns when the JSON-RPC
    ``initialize`` request times out.
    """
    return SimpleNamespace(
        error=(
            "app_server error: JSON-RPC stdio process did not respond to "
            "initialize within 90.0s"
        ),
        exit_code=1,
        events=[],
        tool_call_events=0,
    )


def test_classify_failure_recognises_initialize_timeout() -> None:
    """Pin: ``classify_failure`` returns the dedicated bucket
    ``app_server_initialize_timeout`` for the App Server initialize
    timeout error string. Routes the prose-fallback path to its
    structured handler instead of the generic ``subprocess_nonzero_exit``
    bucket.
    """
    assert (
        diagnostics.classify_failure(_initialize_timeout_result())
        == "app_server_initialize_timeout"
    )


def test_fallback_message_for_initialize_timeout_is_concise() -> None:
    """Pin: prose response for initialize-timeout is a thin pointer at
    the incident, not the apologetic preamble. The typed event is the
    structured surface; prose stays minimal.
    """
    msg = diagnostics.fallback_message(
        backend="codex",
        incident_id="inc-12345678",
        error_class="app_server_initialize_timeout",
    )
    # Must reference the incident id and the diagnose CLI hint.
    assert "inc-12345678" in msg
    assert "claude-anyteam diagnose --incident inc-12345678" in msg
    # Must NOT contain the apologetic generic preamble.
    assert "couldn't generate a reply" not in msg
    # Must reference the typed event path so the lead knows where
    # structured detail lives.
    assert "visibility_degraded" in msg


def test_fallback_message_for_other_classes_keeps_legacy_shape() -> None:
    """Pin: only the initialize-timeout class gets the rewritten concise
    pointer — other failures keep the legacy apologetic preamble. The
    Phase 1 scope is limited; we don't widen the prose rewrite to the
    long tail of failure classes.
    """
    msg = diagnostics.fallback_message(
        backend="codex",
        incident_id="inc-other",
        error_class="subprocess_nonzero_exit",
    )
    assert "couldn't generate a reply" in msg
    assert "inc-other" in msg


def test_prose_fallback_emits_typed_visibility_degraded_for_initialize_timeout(
    events_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: ``_prose_fallback_reply`` for an initialize-timeout
    failure emits a typed ``visibility_degraded`` envelope with
    ``surface="app_server_initialize_timeout"`` and the right
    discriminators. The lead can filter their event log on the surface
    tag instead of grepping prose substrings.
    """
    state = codex_loop.LoopState(settings=_settings(tmp_path))
    state.shutdown_requested = False

    reply = codex_loop._prose_fallback_reply(
        state,
        sender="team-lead",
        result=_initialize_timeout_result(),
    )

    # Prose courtesy response: concise pointer.
    assert "incident_id=inc-" in reply
    assert "claude-anyteam diagnose --incident" in reply

    # Typed event landed in the agent's event log.
    events = pio.read_events("team-codex", "codex-alice")
    matching = [
        e
        for e in events
        if e.kind == "visibility_degraded"
        and e.payload.get("surface") == "app_server_initialize_timeout"
    ]
    assert len(matching) == 1, (
        f"Expected one prose-bound timeout event, got {[e.kind for e in events]}"
    )
    event = matching[0]
    assert event.severity == "error"
    assert event.payload["phase"] == "prose_bound"
    assert event.payload["shutdown_request"] is False
    assert event.payload["sender"] == "team-lead"
    assert event.payload["incident_id"].startswith("inc-")
    assert "did not respond to initialize" in event.payload["raw_backend_error"]
    assert event.visibility.mailbox is True
    assert event.visibility.event_log is True


def test_prose_fallback_marks_shutdown_request_when_in_progress(
    events_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the worst-UX variant from #40: when a shutdown_request is
    in-flight and initialize times out, the typed event carries
    ``shutdown_request=True`` so the lead can filter for the no-op
    shutdown burns specifically. Per team-lead's evidence:
    ``{"type":"shutdown_request"}`` hangs the same 10 minutes.
    """
    state = codex_loop.LoopState(settings=_settings(tmp_path))
    state.shutdown_requested = True

    codex_loop._prose_fallback_reply(
        state,
        sender="team-lead",
        result=_initialize_timeout_result(),
    )

    events = pio.read_events("team-codex", "codex-alice")
    matching = [
        e
        for e in events
        if e.kind == "visibility_degraded"
        and e.payload.get("surface") == "app_server_initialize_timeout"
    ]
    assert len(matching) == 1
    assert matching[0].payload["shutdown_request"] is True


def test_prose_fallback_does_not_emit_typed_event_for_other_classes(
    events_root: Path,
    tmp_path: Path,
) -> None:
    """Pin: the typed event is gated on the initialize-timeout class.
    Other failures (schema validation, generic crashes, etc.) DON'T
    emit the surface-tagged event — keeps the §2 fix narrowly scoped to
    the path the issue thread documents.
    """
    state = codex_loop.LoopState(settings=_settings(tmp_path))
    state.shutdown_requested = False

    other_failure = SimpleNamespace(
        error="codex exec --output-schema produced non-JSON output",
        exit_code=2,
        events=[],
        tool_call_events=0,
    )
    codex_loop._prose_fallback_reply(
        state, sender="peer", result=other_failure
    )

    events = pio.read_events("team-codex", "codex-alice")
    matching = [
        e
        for e in events
        if e.kind == "visibility_degraded"
        and e.payload.get("surface") == "app_server_initialize_timeout"
    ]
    assert matching == [], (
        "Other failure classes must not emit the initialize-timeout "
        "surface tag."
    )


def test_emit_initialize_timeout_helper_lands_in_lead_inbox(
    events_root: Path,
    tmp_path: Path,
) -> None:
    """The ``emit_initialize_timeout_visibility_degraded`` helper fans
    out to BOTH the agent's event log AND the lead's inbox, matching
    the convention of ``emit_peer_steer_rejection`` and
    ``emit_adapter_startup_crash``. The lead must see this at native
    fidelity in the inbox, not via stderr scrape.
    """
    pio.emit_initialize_timeout_visibility_degraded(
        team="team-codex",
        agent="codex-alice",
        backend="codex",
        phase="prose_bound",
        incident_id="inc-abcd1234",
        sender="team-lead",
        shutdown_request=True,
        error="raw error preserved here",
    )

    events = pio.read_events("team-codex", "codex-alice")
    assert len(events) == 1
    event = events[0]
    assert event.kind == "visibility_degraded"
    assert event.payload["surface"] == "app_server_initialize_timeout"
    assert event.payload["phase"] == "prose_bound"
    assert event.payload["shutdown_request"] is True
    assert event.payload["incident_id"] == "inc-abcd1234"
    assert "raw error preserved here" in event.payload["raw_backend_error"]

    # Lead inbox carries the same envelope (downstream filter keys on
    # messageKind=visibility_degraded + surface=...).
    inbox_path = events_root / "team-codex" / "inboxes" / "team-lead.json"
    assert inbox_path.exists()
    inbox = json.loads(inbox_path.read_text())
    matching = [
        json.loads(m["text"])
        for m in inbox
        if m.get("messageKind") == "visibility_degraded"
        and json.loads(m["text"]).get("payload", {}).get("surface")
        == "app_server_initialize_timeout"
    ]
    assert len(matching) == 1
    assert matching[0]["payload"]["incident_id"] == "inc-abcd1234"
