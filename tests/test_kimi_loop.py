from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_anyteam.backends.kimi import loop
from claude_anyteam.backends.kimi.config import KimiSettings


def _settings(tmp_path: Path) -> KimiSettings:
    return KimiSettings(
        team_name="t",
        agent_name="a",
        cwd=tmp_path,
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        kimi_binary="kimi",
    )


def test_backend_run_drops_resume_session_for_ephemeral_invocations(tmp_path: Path, monkeypatch):
    state = loop.KimiLoopState(settings=_settings(tmp_path))
    calls: list[dict] = []

    def fake_run(_prompt: str, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(exit_code=0, structured=None, last_message="", events=[])

    monkeypatch.setattr(loop.headless_invoke, "run", fake_run)

    loop._backend_run(state, "prose", resume_session_id="durable-session", ephemeral=True)
    loop._backend_run(state, "task", resume_session_id="durable-session", ephemeral=False)

    assert calls[0]["resume_session_id"] is None
    assert calls[1]["resume_session_id"] == "durable-session"


def test_kimi_mark_blocked_skips_when_task_already_completed(tmp_path: Path):
    state = loop.KimiLoopState(settings=_settings(tmp_path))
    task = SimpleNamespace(id="42", status="in_progress")
    completed_on_disk = SimpleNamespace(id="42", status="completed")

    update_calls: list = []
    send_calls: list = []

    with (
        patch.object(loop.pio, "get_task", return_value=completed_on_disk),
        patch.object(loop.pio, "update_task", side_effect=lambda *a, **k: update_calls.append((a, k))),
        patch.object(loop.pio, "send_task_blocked", side_effect=lambda *a, **k: send_calls.append((a, k))),
    ):
        loop._mark_blocked(state, task, reason="test reason")

    assert update_calls == []
    assert send_calls == []
