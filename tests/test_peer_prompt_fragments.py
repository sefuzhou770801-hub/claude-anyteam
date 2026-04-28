from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_anyteam import loop as codex_loop
from claude_anyteam import prompts as codex_prompts
from claude_anyteam.backends.gemini import loop as gemini_loop
from claude_anyteam.backends.gemini import prompts as gemini_prompts
from claude_anyteam.backends.gemini.config import GeminiSettings
from claude_anyteam.backends.kimi import loop as kimi_loop
from claude_anyteam.backends.kimi import prompts as kimi_prompts
from claude_anyteam.backends.kimi.config import KimiSettings
from claude_anyteam.capability_manifest import CapabilityManifestCache
from claude_anyteam.codex import CodexResult
from claude_anyteam.config import Settings


TEAM = "t"
SELF = "self"
THREAD_FORK_MARKER = "When follow-up work depends on prior Codex context"
PERMISSION_BRIDGE_MARKER = "When assigning tasks that touch production paths"
TEAM_MESSAGING_MARKERS = (
    "# Team messaging",
    "send_message is exposed lowercase by the wrapper MCP in this session",
    "Plain prose output is NOT visible to teammates",
    "try SendMessage (capitalized)",
    'Do not emit "I cannot deliver" prose',
)
GEMINI_TEAM_MESSAGING_MARKERS = (
    "# Team messaging",
    "mcp_anyteam_send_message is exposed by the wrapper MCP in this session",
    "Plain prose output is NOT visible to teammates",
    "call mcp_anyteam_send_message",
    "underlying wrapper tool is send_message",
    "SendMessage (capitalized)",
    'Do not emit "I cannot deliver" prose',
)


def _task(task_id: str = "7"):
    return SimpleNamespace(
        id=task_id,
        subject="Do work",
        description="Original task body",
        owner="a",
        status="pending",
        blocked_by=[],
    )


def _cache() -> CapabilityManifestCache:
    cache = CapabilityManifestCache(team=TEAM, self_name=SELF)
    cache.manifests = {
        SELF: {
            "agent_name": SELF,
            "capabilities": {
                "structured_output": {
                    "description": "Requester already has structured output.",
                    "when_to_use": "Should be excluded from peer fragments.",
                    "when_not_to": "Do not duplicate requester-native capabilities.",
                }
            },
        },
        "codex-peer": {
            "agent_name": "codex-peer",
            "capabilities": {
                "thread_fork": {
                    "description": "Fork a persisted Codex App Server thread.",
                    "when_to_use": THREAD_FORK_MARKER,
                    "when_not_to": "Do not use for stateless one-shot work.",
                    "failure_modes": ["PARENT_THREAD_NOT_MATERIALIZED", "FORK_UNSUPPORTED"],
                    "delivery_mode": "live",
                    "callable_from_peers": True,
                },
                "structured_output": {
                    "description": "Duplicate requester capability should be omitted.",
                    "when_to_use": "Should not enter prompt fragments.",
                    "callable_from_peers": False,
                },
            },
        },
        "gemini-peer": {
            "agent_name": "gemini-peer",
            "capabilities": {
                "permission_bridge": {
                    "description": "Surface sensitive host-tool use to team-lead for approval.",
                    "when_to_use": PERMISSION_BRIDGE_MARKER,
                    "when_not_to": "Do not route routine read-only tasks here.",
                    "failure_modes": ["APPROVAL_TIMEOUT", "DENIED_BY_TEAM_LEAD"],
                    "callable_from_peers": False,
                },
                "turn_steer": {
                    "description": "Accepts non-lead peer steer.",
                    "when_to_use": "When the peer is on a stale path.",
                    "when_not_to": "Do not steer with low-information nudges.",
                    "failure_modes": ["STEER_AUTH_REJECTED"],
                    "authorization": "any_peer",
                    "callable_from_peers": True,
                },
            },
        },
    }
    return cache


def _fragment() -> str:
    return _cache().peer_prompt_fragments_for(SELF)


def _codex_settings(*, app_server: bool = True) -> Settings:
    return Settings(
        team_name=TEAM,
        agent_name=SELF,
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
        app_server=app_server,
    )


def _gemini_settings() -> GeminiSettings:
    return GeminiSettings(
        team_name=TEAM,
        agent_name=SELF,
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        backend="acp",
    )


def _kimi_settings() -> KimiSettings:
    return KimiSettings(
        team_name=TEAM,
        agent_name=SELF,
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
    )


def _success_result() -> CodexResult:
    return CodexResult(
        exit_code=0,
        structured={"files_changed": [], "summary": "done"},
        last_message='{"files_changed": [], "summary": "done"}',
        events=[],
        error=None,
        session_id="session-1",
    )


def _assert_team_messaging_block(prompt: str) -> None:
    for marker in TEAM_MESSAGING_MARKERS:
        assert marker in prompt


def _assert_gemini_team_messaging_block(prompt: str) -> None:
    for marker in GEMINI_TEAM_MESSAGING_MARKERS:
        assert marker in prompt


def test_empty_team_yields_empty_fragments():
    cache = CapabilityManifestCache(team=TEAM, self_name=SELF)

    assert cache.peer_prompt_fragments_for(SELF) == ""


def test_fragment_drops_when_peer_deregisters():
    cache = _cache()
    assert THREAD_FORK_MARKER in cache.peer_prompt_fragments_for(SELF)

    del cache.manifests["codex-peer"]

    fragment = cache.peer_prompt_fragments_for(SELF)
    assert THREAD_FORK_MARKER not in fragment
    assert PERMISSION_BRIDGE_MARKER in fragment


def test_codex_task_prompt_excludes_self():
    fragment = _fragment()
    prompt = codex_prompts.v7_task_prompt(
        _task(), SELF, TEAM, peer_prompt_fragments=fragment
    )

    assert "# Capabilities of your peers" in prompt
    assert "codex-peer: thread_fork" in prompt
    assert THREAD_FORK_MARKER in prompt
    assert "Requester already has structured output" not in prompt
    assert "Duplicate requester capability should be omitted" not in prompt


def test_codex_routed_prompts_include_team_messaging_block():
    task_prompt = codex_prompts.v7_task_prompt(_task(), SELF, TEAM)
    prose_prompt = codex_prompts.v7_prose_reply_prompt(
        "codex-peer", "please ack", SELF, TEAM
    )

    _assert_team_messaging_block(task_prompt)
    _assert_team_messaging_block(prose_prompt)


def test_kimi_and_gemini_routed_prompts_include_team_messaging_blocks():
    kimi_task = kimi_prompts.task_prompt(_task(), SELF, TEAM)
    kimi_prose = kimi_prompts.prose_reply_prompt(
        "codex-peer", "please ack", SELF, TEAM
    )
    gemini_task = gemini_prompts.task_prompt(_task(), SELF, TEAM)
    gemini_prose = gemini_prompts.prose_reply_prompt(
        "codex-peer", "please ack", SELF, TEAM
    )

    _assert_team_messaging_block(kimi_task)
    _assert_team_messaging_block(kimi_prose)
    _assert_gemini_team_messaging_block(gemini_task)
    _assert_gemini_team_messaging_block(gemini_prose)


def test_r14_fragment_instructs_manifest_query():
    fragment = _fragment()

    assert "REQUIRED capability lookup before peer steering" in fragment
    assert "MUST query mcp_anyteam_capability_manifest" in fragment
    assert "before any peer-steer attempt to gemini-peer" in fragment
    assert "`mcp_anyteam_capability_manifest('gemini-peer', '<primitive>')`" in fragment
    assert "verify acceptance" in fragment
    assert "delivery_mode/expiry_semantics" in fragment
    assert "peer steers will be rejected" in fragment
    assert "waste a turn" in fragment
    assert "visibility_degraded noise" in fragment
    assert "turn_steer" in fragment


def test_homogeneous_paired_still_emits_manifest_lookup():
    cache = CapabilityManifestCache(team=TEAM, self_name=SELF)
    shared_caps = {
        "turn_steer": {
            "description": "Inject text mid-turn.",
            "when_to_use": "When peer is on a stale path.",
            "callable_from_peers": False,
        },
        "structured_output": {
            "description": "Schema-validated task-complete JSON.",
            "callable_from_peers": False,
        },
    }
    cache.manifests = {
        SELF: {"agent_name": SELF, "capabilities": shared_caps},
        "codex-pair-b": {"agent_name": "codex-pair-b", "capabilities": shared_caps},
    }

    fragment = cache.peer_prompt_fragments_for(SELF)

    assert "before any peer-steer attempt to codex-pair-b" in fragment
    assert "`mcp_anyteam_capability_manifest('codex-pair-b', '<primitive>')`" in fragment


def test_gemini_task_prompt_includes_codex_thread_fork_fragment():
    fragment = _fragment()
    prompt = gemini_prompts.task_prompt(
        _task(), SELF, TEAM, peer_prompt_fragments=fragment
    )

    assert "codex-peer: thread_fork" in prompt
    assert THREAD_FORK_MARKER in prompt
    assert "Delivery mode: live" in prompt


def test_codex_task_prompt_includes_gemini_permission_bridge_fragment():
    fragment = _fragment()
    task_prompt = codex_prompts.v7_task_prompt(
        _task(), SELF, TEAM, peer_prompt_fragments=fragment
    )
    prose_prompt = codex_prompts.v7_prose_reply_prompt(
        "gemini-peer", "please route this", SELF, TEAM, peer_prompt_fragments=fragment
    )

    assert "gemini-peer: permission_bridge" in task_prompt
    assert PERMISSION_BRIDGE_MARKER in task_prompt
    assert "gemini-peer: permission_bridge" in prose_prompt
    assert "Authorization: any_peer" in prose_prompt


def test_kimi_task_prompt_includes_codex_capabilities():
    fragment = _fragment()
    prompt = kimi_prompts.task_prompt(
        _task(), SELF, TEAM, peer_prompt_fragments=fragment
    )
    prose_prompt = kimi_prompts.prose_reply_prompt(
        "codex-peer", "hello", SELF, TEAM, peer_prompt_fragments=fragment
    )

    assert "codex-peer: thread_fork" in prompt
    assert THREAD_FORK_MARKER in prompt
    assert "codex-peer: thread_fork" in prose_prompt
    assert "send_message(to='codex-peer'" in prose_prompt


def test_codex_loop_injects_cached_peer_prompt_fragments_into_prose_and_task_prompts():
    state = codex_loop.LoopState(settings=_codex_settings(), peer_manifest_cache=_cache())
    prose_prompts: list[str] = []
    task_prompts: list[str] = []

    with (
        patch.object(
            codex_loop.codex_mod,
            "app_server_invoke",
            side_effect=lambda **kwargs: prose_prompts.append(kwargs["task_prompt"])
            or CodexResult(exit_code=0, structured=None, last_message="ok", events=[]),
        ),
        patch.object(codex_loop.pio, "send_prose"),
    ):
        codex_loop._handle_message(
            state, SimpleNamespace(text="hello", from_="peer", summary="dm")
        )

    with patch.object(
        codex_loop,
        "_execute_task_app_server",
        side_effect=lambda _state, _task, prompt: task_prompts.append(prompt)
        or _success_result(),
    ):
        codex_loop._invoke_codex_for_task(state, _task())

    assert THREAD_FORK_MARKER in prose_prompts[0]
    assert PERMISSION_BRIDGE_MARKER in prose_prompts[0]
    assert THREAD_FORK_MARKER in task_prompts[0]
    assert PERMISSION_BRIDGE_MARKER in task_prompts[0]
    _assert_team_messaging_block(prose_prompts[0])
    _assert_team_messaging_block(task_prompts[0])


def test_gemini_and_kimi_task_loops_inject_cached_peer_prompt_fragments():
    gemini_state = gemini_loop.GeminiLoopState(
        settings=_gemini_settings(), peer_manifest_cache=_cache()
    )
    kimi_state = kimi_loop.KimiLoopState(settings=_kimi_settings(), peer_manifest_cache=_cache())
    gemini_prompts_seen: list[str] = []
    kimi_prompts_seen: list[str] = []

    with (
        patch.object(
            gemini_loop,
            "_backend_run",
            side_effect=lambda _state, prompt, **_kw: gemini_prompts_seen.append(prompt)
            or _success_result(),
        ),
        patch.object(gemini_loop.pio, "update_task"),
        patch.object(gemini_loop.pio, "send_task_complete"),
    ):
        gemini_loop._execute_task(gemini_state, _task("g"))

    with (
        patch.object(
            kimi_loop,
            "_backend_run",
            side_effect=lambda _state, prompt, **_kw: kimi_prompts_seen.append(prompt)
            or _success_result(),
        ),
        patch.object(kimi_loop.pio, "update_task"),
        patch.object(kimi_loop.pio, "send_task_complete"),
    ):
        kimi_loop._execute_task(kimi_state, _task("k"))

    assert THREAD_FORK_MARKER in gemini_prompts_seen[0]
    assert PERMISSION_BRIDGE_MARKER in gemini_prompts_seen[0]
    assert THREAD_FORK_MARKER in kimi_prompts_seen[0]
    assert PERMISSION_BRIDGE_MARKER in kimi_prompts_seen[0]
