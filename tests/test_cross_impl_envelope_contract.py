"""Cross-implementation visibility-envelope contract.

This is the pre-stress canary for gaps like #46: every supported backend is
driven through its normal adapter loop, with real registration/claim/prose/
idle/shutdown protocol I/O and only the external CLI/client boundary faked.
If a backend drops a required envelope kind, the parametrized case names the
backend that drifted.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

import pytest
from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]
from claude_teams import tasks as cs_tasks  # type: ignore[import-untyped]
from claude_teams import teams as cs_teams  # type: ignore[import-untyped]

from claude_anyteam import app_server as app_server_mod
from claude_anyteam import capability_manifest as capability_manifest_mod
from claude_anyteam import loop as codex_loop
from claude_anyteam import protocol_io as pio
from claude_anyteam import registration as registration_mod
from claude_anyteam.backends.gemini import acp as gemini_acp
from claude_anyteam.backends.gemini import invoke as gemini_headless
from claude_anyteam.backends.gemini import loop as gemini_loop
from claude_anyteam.backends.gemini.config import GeminiSettings
from claude_anyteam.backends.kimi import invoke as kimi_headless
from claude_anyteam.backends.kimi import loop as kimi_loop
from claude_anyteam.backends.kimi.config import KimiSettings
from claude_anyteam.config import Settings
from claude_anyteam.messages import VisibilityEventKind


CANONICAL_VISIBILITY_KINDS = frozenset(get_args(VisibilityEventKind))
REQUIRED_EVENT_LOG_KINDS = frozenset(
    {
        "agent_registered",
        "turn_started",
        "tool_event",
    }
)
TERMINAL_EVENT_KINDS = frozenset({"turn_completed", "turn_failed"})
REQUIRED_MAILBOX_TYPES = frozenset({"idle_notification"})
SHUTDOWN_RESPONSE_TYPES = frozenset({"shutdown_approved", "shutdown_rejected"})


@dataclass(frozen=True)
class BackendContract:
    case_id: str
    agent_name: str
    runtime_backend: str
    settings_factory: Callable[[str, str, Path], Any]
    run_adapter: Callable[[Any], int]
    patch_backend: Callable[[pytest.MonkeyPatch, str, str, Path], None]
    loop_module: Any


def _codex_settings(team: str, agent: str, cwd: Path) -> Settings:
    return Settings(
        team_name=team,
        agent_name=agent,
        cwd=cwd,
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
        app_server=True,
        turn_timeout_s=60.0,
        non_progress_warn_s=60.0,
    )


def _gemini_settings(backend: str) -> Callable[[str, str, Path], GeminiSettings]:
    def _factory(team: str, agent: str, cwd: Path) -> GeminiSettings:
        return GeminiSettings(
            team_name=team,
            agent_name=agent,
            cwd=cwd,
            poll_interval_s=0.01,
            color="cyan",
            plan_mode_required=False,
            gemini_binary="gemini",
            gemini_home=cwd / f".gemini-{backend}",
            backend=backend,  # type: ignore[arg-type]
            trust_mode="trusted",
        )

    return _factory


def _kimi_settings(team: str, agent: str, cwd: Path) -> KimiSettings:
    return KimiSettings(
        team_name=team,
        agent_name=agent,
        cwd=cwd,
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        kimi_binary="kimi",
        kimi_home=cwd / ".kimi-headless",
        backend="headless",
        thinking="off",
    )


class _NotificationQueue:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def reset(self, items: list[dict[str, Any]]) -> None:
        self.items = list(items)

    def get(self, timeout: float | None = None) -> dict[str, Any]:
        if self.items:
            return self.items.pop(0)
        raise RuntimeError("empty notification queue")


class _ContractAppServerClient:
    turn_count = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.notifications = _NotificationQueue()

    def start(self) -> None:
        pass

    def initialize(self, **_kwargs) -> dict[str, Any]:
        return {}

    def thread_start(self, **kwargs: Any) -> str:
        return f"thread-{type(self).turn_count + 1}"

    def turn_start(self, **kwargs: Any) -> str:
        type(self).turn_count += 1
        turn_id = f"turn-{type(self).turn_count}"
        if kwargs.get("output_schema") is not None:
            self.notifications.reset(
                [
                    {
                        "method": "item/completed",
                        "params": {
                            "item": {
                                "type": "commandExecution",
                                "command": "printf contract",
                                "exitCode": 0,
                            }
                        },
                    },
                    {
                        "method": "item/completed",
                        "params": {
                            "item": {
                                "type": "agentMessage",
                                "text": json.dumps(
                                    {"files_changed": [], "summary": "contract task done"}
                                ),
                            }
                        },
                    },
                    {"method": "turn/completed", "params": {"turn": {"status": "ok"}}},
                ]
            )
        else:
            self.notifications.reset(
                [
                    {
                        "method": "item/completed",
                        "params": {
                            "item": {
                                "type": "commandExecution",
                                "command": "printf prose-contract",
                                "exitCode": 0,
                            }
                        },
                    },
                    {"method": "turn/completed", "params": {"turn": {"status": "ok"}}},
                ]
            )
        return turn_id

    def close(self) -> None:
        pass


class _RecoveringAppServerClient:
    instances: list["_RecoveringAppServerClient"] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.notifications = _NotificationQueue()
        self.turn_start_kwargs: list[dict[str, Any]] = []
        self.reconnects = 0
        type(self).instances.append(self)

    def start(self) -> None:
        pass

    def initialize(self, **_kwargs) -> dict[str, Any]:
        return {}

    def thread_start(self, **kwargs: Any) -> str:
        return "thread-recover"

    def turn_start(self, **kwargs: Any) -> str:
        self.turn_start_kwargs.append(kwargs)
        if len(self.turn_start_kwargs) == 1:
            self.notifications.reset(
                [
                    {
                        "method": "item/completed",
                        "params": {
                            "item": {
                                "type": "commandExecution",
                                "command": "printf before-crash",
                                "exitCode": 0,
                            }
                        },
                    }
                ]
            )
            return "turn-original"

        self.notifications.reset(
            [
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "type": "agentMessage",
                            "text": json.dumps(
                                {
                                    "files_changed": ["recovered.txt"],
                                    "summary": "recovered task done",
                                }
                            ),
                        }
                    },
                },
                {"method": "turn/completed", "params": {"turn": {"status": "ok"}}},
            ]
        )
        return "turn-recovery"

    def is_transport_alive(self) -> bool:
        # After the first turn's only notification is drained, simulate the
        # App Server/WebSocket transport disappearing mid-turn. The reconnect
        # path flips `reconnects`, after which the recovery turn is healthy.
        first_turn_drained = (
            len(self.turn_start_kwargs) == 1
            and not self.notifications.items
            and self.reconnects == 0
        )
        return not first_turn_drained

    def transport_status(self) -> dict[str, Any]:
        return {"fake": "down" if not self.is_transport_alive() else "up"}

    def reconnect_and_resume(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["thread_id"] == "thread-recover"
        self.reconnects += 1
        self.notifications.reset([])
        return {
            "thread": {
                "id": "thread-recover",
                "turns": [
                    {
                        "id": "turn-original",
                        "status": "inProgress",
                        "items": [],
                    }
                ],
            }
        }

    def close(self) -> None:
        pass


class _FailingRecoveryAppServerClient(_RecoveringAppServerClient):
    instances: list["_FailingRecoveryAppServerClient"] = []

    def reconnect_and_resume(self, **kwargs: Any) -> dict[str, Any]:
        self.reconnects += 1
        raise app_server_mod.AppServerError("simulated reconnect failure")


def _patch_codex_backend(
    monkeypatch: pytest.MonkeyPatch,
    team: str,
    agent: str,
    cwd: Path,
) -> None:
    _ContractAppServerClient.turn_count = 0
    monkeypatch.setattr(codex_loop.codex_mod, "feature_test", lambda *a, **k: None)
    monkeypatch.setattr(app_server_mod, "AppServerClient", _ContractAppServerClient)


class _ContractGeminiAcpClient:
    instances: list["_ContractGeminiAcpClient"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.notifications: list[dict[str, Any]] = []
        self.session_new_kwargs: dict[str, Any] | None = None
        type(self).instances.append(self)

    def start(self) -> None:
        pass

    def close(self) -> None:
        pass

    def initialize(self, **_kwargs) -> dict[str, Any]:
        return {"protocolVersion": 1}

    def session_new(self, **kwargs: Any) -> dict[str, str]:
        self.session_new_kwargs = kwargs
        return {"sessionId": f"acp-session-{len(type(self).instances)}"}

    def set_session_mode(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    def unstable_set_session_model(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    def session_prompt(self, **kwargs: Any) -> dict[str, str]:
        prompt = kwargs["prompt"]
        session_id = kwargs["session_id"]
        self.notifications.extend(
            [
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "tool-1",
                            "title": "mcp_anyteam_send_message",
                            "status": "in_progress",
                        },
                    },
                },
            ]
        )
        if "# Output contract" in prompt:
            self.notifications.append(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {
                                "type": "text",
                                "text": json.dumps(
                                    {"files_changed": [], "summary": "contract task done"}
                                ),
                            },
                        },
                    },
                }
            )
        return {"stopReason": "end_turn"}

    def drain_notifications(self) -> list[dict[str, Any]]:
        return list(self.notifications)


def _patch_gemini_acp_backend(
    monkeypatch: pytest.MonkeyPatch,
    team: str,
    agent: str,
    cwd: Path,
) -> None:
    _ContractGeminiAcpClient.instances = []
    monkeypatch.setattr(gemini_loop, "_backend_feature_test", lambda settings: None)
    monkeypatch.setattr(gemini_loop, "_backend_auth_preflight", lambda settings, *, gemini_home: None)
    monkeypatch.setattr(gemini_loop.crash_hygiene, "run_startup_recovery", lambda **kwargs: None)
    monkeypatch.setattr(gemini_loop.crash_hygiene, "mark_adapter_start", lambda *a, **k: None)
    monkeypatch.setattr(gemini_loop.crash_hygiene, "mark_clean_shutdown", lambda *a, **k: None)
    monkeypatch.setattr(gemini_acp, "GeminiAcpClient", _ContractGeminiAcpClient)
    monkeypatch.setattr(gemini_acp.invoke.shutil, "which", lambda name: f"/bin/{name}")


def _gemini_stream(prompt: str) -> str:
    events: list[dict[str, Any]] = [
        {"type": "init", "session_id": "gemini-session-1"},
        {"type": "tool_use", "tool_name": "mcp_anyteam_send_message"},
    ]
    if "# Output contract" in prompt:
        events.append(
            {
                "type": "message",
                "role": "assistant",
                "content": json.dumps(
                    {"files_changed": [], "summary": "contract task done"}
                ),
            }
        )
    events.append({"type": "result", "status": "success"})
    return "\n".join(json.dumps(event) for event in events)


def _patch_gemini_headless_backend(
    monkeypatch: pytest.MonkeyPatch,
    team: str,
    agent: str,
    cwd: Path,
) -> None:
    monkeypatch.setattr(gemini_loop, "_backend_feature_test", lambda settings: None)
    monkeypatch.setattr(gemini_headless.shutil, "which", lambda name: f"/bin/{name}")

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        prompt = args[args.index("--prompt") + 1]
        return subprocess.CompletedProcess(args, 0, stdout=_gemini_stream(prompt), stderr="")

    monkeypatch.setattr(gemini_headless.subprocess, "run", fake_run)


def _kimi_stream(prompt: str) -> str:
    events: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": [],
            "tool_calls": [
                {
                    "type": "function",
                    "id": "kimi-tool-1",
                    "function": {"name": "Shell", "arguments": '{"cmd":"echo contract"}'},
                }
            ],
        }
    ]
    if "# Output contract" in prompt:
        events.append(
            {
                "role": "assistant",
                "content": json.dumps(
                    {"files_changed": [], "summary": "contract task done"}
                ),
            }
        )
    return "\n".join(json.dumps(event) for event in events)


def _patch_kimi_headless_backend(
    monkeypatch: pytest.MonkeyPatch,
    team: str,
    agent: str,
    cwd: Path,
) -> None:
    monkeypatch.setattr(kimi_loop, "_backend_feature_test", lambda settings: None)
    monkeypatch.setattr(kimi_headless.shutil, "which", lambda name: f"/bin/{name}")

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        prompt = args[args.index("-p") + 1]
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=_kimi_stream(prompt),
            stderr="To resume this session: kimi -r kimi-session-1\n",
        )

    monkeypatch.setattr(kimi_headless.subprocess, "run", fake_run)


BACKENDS = [
    BackendContract(
        case_id="codex",
        agent_name="codex-contract",
        runtime_backend="codex_app_server",
        settings_factory=_codex_settings,
        run_adapter=codex_loop.run,
        patch_backend=_patch_codex_backend,
        loop_module=codex_loop,
    ),
    BackendContract(
        case_id="gemini-acp",
        agent_name="gemini-acp-contract",
        runtime_backend="gemini_acp",
        settings_factory=_gemini_settings("acp"),
        run_adapter=gemini_loop.run,
        patch_backend=_patch_gemini_acp_backend,
        loop_module=gemini_loop,
    ),
    BackendContract(
        case_id="gemini-headless",
        agent_name="gemini-headless-contract",
        runtime_backend="gemini_headless",
        settings_factory=_gemini_settings("headless"),
        run_adapter=gemini_loop.run,
        patch_backend=_patch_gemini_headless_backend,
        loop_module=gemini_loop,
    ),
    BackendContract(
        case_id="kimi-headless",
        agent_name="kimi-headless-contract",
        runtime_backend="kimi_headless",
        settings_factory=_kimi_settings,
        run_adapter=kimi_loop.run,
        patch_backend=_patch_kimi_headless_backend,
        loop_module=kimi_loop,
    ),
]


def _write_team_config(teams_root: Path, team: str, cwd: Path) -> None:
    team_dir = teams_root / team
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "inboxes").mkdir(exist_ok=True)
    (team_dir / "inboxes" / "team-lead.json").write_text("[]", encoding="utf-8")
    config = {
        "name": team,
        "description": "",
        "createdAt": 0,
        "leadAgentId": f"team-lead@{team}",
        "leadSessionId": "lead-session",
        "members": [
            {
                "agentId": f"team-lead@{team}",
                "name": "team-lead",
                "agentType": "team-lead",
                "model": "claude-opus-4-6",
                "joinedAt": 0,
                "tmuxPaneId": "",
                "cwd": str(cwd),
                "subscriptions": [],
            }
        ],
    }
    (team_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")


def _write_fake_task(tasks_root: Path, team: str) -> None:
    task_dir = tasks_root / team
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / ".lock").touch()
    task = {
        "id": "1",
        "subject": "contract task",
        "description": "exercise the envelope contract",
        "activeForm": "",
        "status": "pending",
        "owner": None,
        "blockedBy": [],
        "blocks": [],
    }
    (task_dir / "1.json").write_text(json.dumps(task), encoding="utf-8")


def _install_protocol_roots(
    monkeypatch: pytest.MonkeyPatch,
    *,
    teams_root: Path,
    tasks_root: Path,
) -> None:
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", teams_root)
    monkeypatch.setattr(cs_tasks, "TASKS_DIR", tasks_root)
    monkeypatch.setattr(cs_teams, "TEAMS_DIR", teams_root)
    monkeypatch.setattr(cs_teams, "TASKS_DIR", tasks_root)
    monkeypatch.setattr(registration_mod, "TEAMS_ROOT", teams_root)
    monkeypatch.setattr(capability_manifest_mod, "TEAMS_ROOT", teams_root)


def _lead_message_types(teams_root: Path, team: str) -> list[str]:
    path = teams_root / team / "inboxes" / "team-lead.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for row in raw:
        if isinstance(row, dict) and row.get("messageKind"):
            out.append(str(row["messageKind"]))
        try:
            body = json.loads(row.get("text", ""))
        except (AttributeError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(body, dict):
            kind = body.get("kind") or body.get("type")
            if isinstance(kind, str):
                out.append(kind)
    return out


def _enqueue_prose_once_after_task_complete(
    monkeypatch: pytest.MonkeyPatch,
    *,
    team: str,
    agent: str,
) -> None:
    original_send_task_complete = pio.send_task_complete
    sent = False

    def wrapped_send_task_complete(*args: Any, **kwargs: Any) -> None:
        nonlocal sent
        original_send_task_complete(*args, **kwargs)
        if not sent:
            sent = True
            cs_messaging.send_plain_message(
                team,
                "team-lead",
                agent,
                "Please acknowledge this prose contract ping via send_message.",
                summary="contract_prose",
            )

    monkeypatch.setattr(pio, "send_task_complete", wrapped_send_task_complete)


def _install_shutdown_after_first_idle_sleep(
    monkeypatch: pytest.MonkeyPatch,
    *,
    case: BackendContract,
    team: str,
    agent: str,
) -> None:
    sent_shutdown = False

    def trigger_shutdown(label: str) -> None:
        nonlocal sent_shutdown
        if not sent_shutdown:
            sent_shutdown = True
            cs_messaging.send_shutdown_request(
                team,
                agent,
                reason="cross-impl envelope contract complete",
            )
            return
        raise AssertionError(
            f"{case.case_id} adapter idled again ({label}) after shutdown was queued"
        )

    def fake_sleep(_seconds: float) -> None:
        trigger_shutdown("time.sleep")

    monkeypatch.setattr(case.loop_module.time, "sleep", fake_sleep)

    # Phase4 #28 (event-driven inbox watcher): adapter loops now wait via
    # WatchInbox.wait_for_change instead of time.sleep, so the legacy fake_sleep
    # hook above never fires. Mirror the same shutdown-on-first-idle injection
    # at the new wait point so the contract still ships shutdown after the
    # adapter completes its first turn.
    from claude_anyteam import watch_inbox as _watch_inbox

    real_wait_for_change = _watch_inbox.WatchInbox.wait_for_change

    def fake_wait_for_change(self: _watch_inbox.WatchInbox, timeout_s: float) -> bool:
        trigger_shutdown("WatchInbox.wait_for_change")
        return False

    monkeypatch.setattr(
        _watch_inbox.WatchInbox, "wait_for_change", fake_wait_for_change
    )


@pytest.mark.parametrize("case", BACKENDS, ids=[case.case_id for case in BACKENDS])
def test_backend_emits_required_envelope_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: BackendContract,
) -> None:
    team = f"contract-{case.case_id}"
    cwd = tmp_path / "work"
    cwd.mkdir()
    teams_root = tmp_path / "teams"
    tasks_root = tmp_path / "tasks"
    _install_protocol_roots(monkeypatch, teams_root=teams_root, tasks_root=tasks_root)
    _write_team_config(teams_root, team, cwd)
    _write_fake_task(tasks_root, team)

    _enqueue_prose_once_after_task_complete(monkeypatch, team=team, agent=case.agent_name)
    _install_shutdown_after_first_idle_sleep(
        monkeypatch,
        case=case,
        team=team,
        agent=case.agent_name,
    )
    case.patch_backend(monkeypatch, team, case.agent_name, cwd)

    settings = case.settings_factory(team, case.agent_name, cwd)
    assert case.run_adapter(settings) == 0

    events = pio.read_events(team, case.agent_name)
    event_kinds = [event.kind for event in events]
    mailbox_types = _lead_message_types(teams_root, team)

    assert REQUIRED_EVENT_LOG_KINDS <= set(event_kinds)
    assert set(event_kinds) <= CANONICAL_VISIBILITY_KINDS
    assert TERMINAL_EVENT_KINDS & set(event_kinds)
    assert event_kinds.count("turn_started") >= 2, event_kinds
    assert any(
        event.kind == "turn_started" and event.payload.get("mode") == "prose"
        for event in events
    ), event_kinds
    assert REQUIRED_MAILBOX_TYPES <= set(mailbox_types)
    assert SHUTDOWN_RESPONSE_TYPES & set(mailbox_types)
    assert "agent_registered" in mailbox_types

    runtime_events = [
        event
        for event in events
        if event.kind
        in {"turn_started", "tool_event", "turn_completed", "turn_failed"}
    ]
    assert runtime_events
    assert {event.backend for event in runtime_events} == {case.runtime_backend}


def test_codex_app_server_transport_loss_reconnects_and_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecoveringAppServerClient.instances = []
    monkeypatch.setattr(app_server_mod, "AppServerClient", _RecoveringAppServerClient)
    events: list[Any] = []

    result = codex_loop.codex_mod.app_server_invoke(
        task_prompt="finish the task",
        cwd=tmp_path,
        schema={"type": "object"},
        settings_team="transport-contract",
        settings_agent="codex-recovering",
        codex_binary="codex",
        overall_timeout_s=10.0,
        non_progress_warn_s=60.0,
        event_sink=events.append,
    )

    assert result.exit_code == 0, result.error
    assert result.structured == {
        "files_changed": ["recovered.txt"],
        "summary": "recovered task done",
    }
    assert result.session_id == "thread-recover"
    assert result.tool_call_events == 1

    client = _RecoveringAppServerClient.instances[0]
    assert client.reconnects == 1
    assert len(client.turn_start_kwargs) == 2
    assert "Transport recovery" in client.turn_start_kwargs[1]["text"]

    kinds = [event.kind for event in events]
    assert "turn_warning" in kinds
    assert "turn_progress" in kinds
    assert "turn_failed" not in kinds
    assert kinds[-1] == "turn_completed"

    warning = next(event for event in events if event.kind == "turn_warning")
    assert warning.payload["surface"] == "codex_app_server_transport"
    assert warning.payload["action"] == "reconnect_and_resume"

    recovery_progress = [
        event
        for event in events
        if event.kind == "turn_progress"
        and event.payload.get("action") == "recovery_turn_started"
    ]
    assert recovery_progress


def test_codex_app_server_reconnect_failure_stays_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FailingRecoveryAppServerClient.instances = []
    monkeypatch.setattr(app_server_mod, "AppServerClient", _FailingRecoveryAppServerClient)
    events: list[Any] = []

    result = codex_loop.codex_mod.app_server_invoke(
        task_prompt="finish the task",
        cwd=tmp_path,
        schema={"type": "object"},
        settings_team="transport-contract",
        settings_agent="codex-failing-recovery",
        codex_binary="codex",
        overall_timeout_s=10.0,
        non_progress_warn_s=60.0,
        event_sink=events.append,
    )

    assert result.exit_code == 1
    assert result.structured is None
    assert result.error is not None
    assert "reconnect/resume failed" in result.error

    degraded = [
        event
        for event in events
        if event.kind == "visibility_degraded"
        and event.payload.get("surface") == "codex_app_server_transport"
    ]
    assert degraded
    assert degraded[0].payload["reason"] == "reconnect_failed"
    assert degraded[0].visibility.mailbox is True
    assert degraded[0].visibility.task_state is True
    assert any(event.kind == "turn_failed" for event in events)


def test_contract_expected_kinds_are_canonical() -> None:
    """Forward-compat canary: extend the contract table as kinds are added."""

    asserted_kinds = REQUIRED_EVENT_LOG_KINDS | TERMINAL_EVENT_KINDS
    assert asserted_kinds <= CANONICAL_VISIBILITY_KINDS
