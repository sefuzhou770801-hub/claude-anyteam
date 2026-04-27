from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from claude_anyteam import codex as codex_mod
from claude_anyteam import loop as codex_loop
from claude_anyteam.backends.gemini import loop as gemini_loop
from claude_anyteam.backends.gemini.config import GeminiSettings
from claude_anyteam.backends.kimi import loop as kimi_loop
from claude_anyteam.backends.kimi.config import KimiSettings
from claude_anyteam.config import Settings


TEAM = "peer-steer-authz"
PEER = "codex-exec"


def _codex_settings(tmp_path: Path) -> Settings:
    return Settings(
        team_name=TEAM,
        agent_name="codex-app-server",
        cwd=tmp_path,
        poll_interval_s=1.0,
        color="red",
        plan_mode_required=False,
        codex_binary="codex",
        app_server=True,
    )


def _gemini_settings(tmp_path: Path, *, backend: str) -> GeminiSettings:
    return GeminiSettings(
        team_name=TEAM,
        agent_name=f"gemini-{backend}",
        cwd=tmp_path,
        poll_interval_s=1.0,
        color="green",
        plan_mode_required=False,
        backend=backend,
    )


def _kimi_settings(tmp_path: Path) -> KimiSettings:
    return KimiSettings(
        team_name=TEAM,
        agent_name="kimi-headless",
        cwd=tmp_path,
        poll_interval_s=1.0,
        color="orange",
        plan_mode_required=False,
    )


def _steer_msg(sender: str, message: str = "Please use the revised constraint."):
    return SimpleNamespace(
        from_=sender,
        text=json.dumps({"type": "steer", "from": sender, "message": message}),
    )


def _codex_result() -> codex_mod.CodexResult:
    return codex_mod.CodexResult(
        exit_code=0,
        structured={"files_changed": [], "summary": "done"},
        last_message='{"files_changed":[],"summary":"done"}',
        events=[],
        error=None,
    )


def test_peer_steer_from_non_lead_succeeds_for_gemini_acp(tmp_path: Path):
    state = gemini_loop.GeminiLoopState(
        settings=_gemini_settings(tmp_path, backend="acp"),
    )

    gemini_loop._handle_message(state, _steer_msg(PEER))

    assert len(state.queued_steers) == 1
    assert state.queued_steers[0].message == "Please use the revised constraint."


def test_peer_steer_from_non_lead_rejected_for_kimi_headless(
    monkeypatch, tmp_path: Path
):
    state = kimi_loop.KimiLoopState(settings=_kimi_settings(tmp_path))
    warnings: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        kimi_loop.logger,
        "warn",
        lambda msg, **fields: warnings.append((msg, fields)),
    )

    kimi_loop._handle_message(state, _steer_msg(PEER))

    assert state.queued_steers == []
    assert warnings == [
        (
            "kimi.steer.rejected",
            {
                "sender": PEER,
                "reason": "not_team_lead_and_capability_not_declared",
            },
        )
    ]


def test_peer_steer_from_non_lead_rejected_for_codex_app_server(
    monkeypatch, tmp_path: Path
):
    metadata = codex_loop._backend_metadata(_codex_settings(tmp_path))
    queue = codex_mod.SteerQueue(capabilities=metadata.capabilities)
    warnings: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        codex_mod.logger,
        "warn",
        lambda msg, **fields: warnings.append((msg, fields)),
    )

    assert "accepts_peer_steer" not in metadata.capabilities
    assert queue.push("mid-task peer steer", sender=PEER) is False
    assert queue.pop_nowait() is None
    assert warnings == [
        (
            "app_server.steer.rejected",
            {
                "sender": PEER,
                "reason": "not_team_lead_and_capability_not_declared",
            },
        )
    ]


def test_codex_app_server_mid_turn_peer_steer_rejected_by_runtime_capabilities(
    monkeypatch, tmp_path: Path
):
    schema_path = tmp_path / "task-complete.schema.json"
    schema_path.write_text("{}", encoding="utf-8")
    state = codex_loop.LoopState(settings=_codex_settings(tmp_path))
    warnings: list[tuple[str, dict]] = []

    def fake_invoke(**kwargs):
        kwargs["mid_turn_hook"]()
        assert kwargs["steer_queue"].pop_nowait() is None
        return _codex_result()

    monkeypatch.setattr(codex_loop.codex_mod, "TASK_COMPLETE_SCHEMA", schema_path)
    monkeypatch.setattr(
        codex_loop.pio,
        "read_own_inbox",
        lambda *_args: [_steer_msg(PEER)],
    )
    monkeypatch.setattr(codex_loop.codex_mod, "app_server_invoke", fake_invoke)
    monkeypatch.setattr(
        codex_mod.logger,
        "warn",
        lambda msg, **fields: warnings.append((msg, fields)),
    )

    codex_loop._execute_task_app_server(
        state,
        SimpleNamespace(id="42"),
        prompt="do work",
    )

    assert warnings == [
        (
            "app_server.steer.rejected",
            {
                "sender": PEER,
                "reason": "not_team_lead_and_capability_not_declared",
            },
        )
    ]


def test_team_lead_steer_allowed_without_peer_steer_capability(tmp_path: Path):
    metadata = codex_loop._backend_metadata(_codex_settings(tmp_path))
    queue = codex_mod.SteerQueue(capabilities=metadata.capabilities)

    assert queue.push("lead steer", sender="team-lead") is True
    assert queue.pop_nowait() == "lead steer"
