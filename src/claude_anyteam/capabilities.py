"""Canonical capability declarations for routed teammate registration.

R11 in ``docs/internal/protocol-rev/09-implementation-roadmap.md`` adds the
flat ``members[].capabilities`` roster layer. 08 §6.3 defines the
Agent Card ``capabilities()`` hook this cheap list is derived from; R12/R13
add the rich manifest that the wrapper MCP returns from cache.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

CAPABILITY_NAMES = frozenset(
    {
        "turn_steer",
        "thread_fork",
        "permission_bridge",
        "live_tool_events",
        "structured_output",
        "large_context",
        "accepts_peer_steer",
        "native_swarm",
    }
)


CODEX_APP_SERVER_CAPABILITIES = [
    "turn_steer",
    "thread_fork",
    "live_tool_events",
    "structured_output",
    # Q4 (per opus-arch-impl): Codex App Server's SteerQueue.push currently
    # accepts any sender unintentionally (CD-6 footnote). We declare false
    # here for conservative shipping — reflects the explicit *intent*, not
    # the accidental implementation. R15-finalize (codex-impl-peer) will
    # wire SteerQueue to honor this declaration. Flip to "accepts_peer_steer"
    # post-verification once R15 enforcement test passes for non-lead
    # senders against a Codex App Server recipient.
]

CODEX_EXEC_CAPABILITIES = ["structured_output"]

GEMINI_ACP_CAPABILITIES = [
    # ACP delivery is wired at the next turn boundary today, but R11 declares
    # this cheap flag ahead of the richer R12/R13 manifest's delivery-mode
    # detail so peers can discover that steer exists at all.
    "turn_steer",
    "permission_bridge",
    "live_tool_events",
    "accepts_peer_steer",
]

GEMINI_HEADLESS_CAPABILITIES: list[str] = []

KIMI_HEADLESS_CAPABILITIES = ["large_context", "native_swarm"]

CAPABILITY_MANIFEST_SCHEMA_VERSION = 1
CAPABILITY_MANIFEST_VERSION = "1"

_CAPABILITY_DISPLAY_NAMES = {
    "turn_steer": "turn/steer (`turn_steer`)",
    "thread_fork": "thread/fork (`thread_fork`)",
    "permission_bridge": "permission bridge (`permission_bridge`)",
    "live_tool_events": "live tool events (`live_tool_events`)",
    "structured_output": "structured output (`structured_output`)",
    "large_context": "large context (`large_context`)",
    "accepts_peer_steer": "peer steer acceptance (`accepts_peer_steer`)",
    "native_swarm": "native swarm (`native_swarm`)",
}

_BASE_CAPABILITY_MANIFEST: dict[str, dict[str, Any]] = {
    "turn_steer": {
        "version": "1",
        "schema": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string", "maxLength": 8192},
                "task_id": {"type": ["string", "null"]},
                "priority": {"type": "string", "enum": ["normal", "urgent"], "default": "normal"},
                "expires_after_turns": {"type": "integer", "minimum": 1, "default": 1},
            },
        },
        "description": "Inject text mid-turn or at a turn boundary to redirect a teammate's reasoning.",
        "when_to_use": (
            "Use when you see the teammate pursuing a stale path, when a peer discovers a "
            "constraint that must be applied before the task continues, or when context should "
            "arrive without restarting the task."
        ),
        "when_not_to": (
            "Do not steer with low-information nudges such as 'are you done?', and avoid "
            "steering while a structured tool call depends on stable inputs."
        ),
        "failure_modes": [
            "RACE_LOST_NO_TURN_IN_FLIGHT",
            "STEER_BUFFERED_NEXT_BOUNDARY",
            "STEER_AUTH_REJECTED",
            "STEER_PAYLOAD_OVERFLOW",
        ],
    },
    "thread_fork": {
        "version": "1",
        "schema": {
            "type": "object",
            "properties": {
                "parent_thread_id": {"type": "string"},
                "task_id": {"type": ["string", "null"]},
            },
        },
        "description": "Fork a persisted Codex App Server thread so future work inherits prior-task context.",
        "when_to_use": (
            "Ask a Codex App Server teammate to use thread continuity when a follow-up task "
            "depends on substantial context from its previous task."
        ),
        "when_not_to": (
            "Do not depend on thread_fork for stateless one-shot tasks, or after a failed turn "
            "whose context should not poison the next attempt."
        ),
        "failure_modes": ["PARENT_THREAD_NOT_MATERIALIZED", "FORK_UNSUPPORTED", "FORK_CONTEXT_STALE"],
        "callable_from_peers": True,
    },
    "permission_bridge": {
        "version": "1",
        "schema": {
            "type": "object",
            "required": ["request_id", "decision"],
            "properties": {
                "request_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["allow_once", "allow_session", "deny"]},
                "reason": {"type": "string"},
            },
        },
        "description": "Surface sensitive host-tool use to team-lead for interactive approval before execution.",
        "when_to_use": (
            "Route approval-sensitive work here when tasks touch production paths, secrets, "
            "external networks, or other operations the lead wants explicitly gated."
        ),
        "when_not_to": (
            "Do not route routine read-only or simple test tasks here solely for the bridge; "
            "approval prompts add latency without value."
        ),
        "failure_modes": [
            "APPROVAL_TIMEOUT",
            "APPROVAL_BRIDGE_ERROR",
            "APPROVAL_CONTEXT_MISSING",
            "DENIED_BY_TEAM_LEAD",
        ],
        "callable_from_peers": False,
    },
    "live_tool_events": {
        "version": "1",
        "schema": {"type": "object", "additionalProperties": True},
        "description": "Emit or expose host-tool activity while a turn is running rather than only at task completion.",
        "when_to_use": "Prefer this teammate when peers or the lead need live operational visibility into long-running work.",
        "when_not_to": "Not directly callable; this is an observability signal for routing and expectations.",
        "failure_modes": ["TOOL_EVENT_STREAM_DEGRADED", "HOST_EVENT_SHAPE_CHANGED"],
        "callable_from_peers": False,
    },
    "structured_output": {
        "version": "1",
        "schema": {"type": "object", "required": ["files_changed", "summary"]},
        "description": "Return schema-validated task-complete JSON with files_changed and summary fields.",
        "when_to_use": "Use for coding tasks where the lead needs machine-readable completion metadata.",
        "when_not_to": "Not directly callable by peers; it describes final-output fidelity.",
        "failure_modes": ["SCHEMA_VALIDATION_FAILED", "OUTPUT_SCHEMA_UNSUPPORTED", "RETRY_EXHAUSTED"],
        "callable_from_peers": False,
    },
    "large_context": {
        "version": "1",
        "schema": {"type": "object", "properties": {"context_tokens": {"type": "integer", "minimum": 100000}}},
        "description": "Handle very large prompts or repositories with a context window above 100k tokens.",
        "when_to_use": "Route broad audits, large-file synthesis, or multi-document reasoning to this teammate.",
        "when_not_to": "Do not use large_context as a substitute for precise task scoping when a smaller teammate is faster.",
        "failure_modes": ["CONTEXT_TOO_LARGE", "CONTEXT_TRUNCATED", "MODEL_CONTEXT_POLICY_CHANGED"],
        "callable_from_peers": False,
    },
    "accepts_peer_steer": {
        "version": "1",
        "schema": {"type": "boolean"},
        "description": "Declare that non-lead peers may send steer messages to this teammate.",
        "when_to_use": "Check before steering another peer directly; if absent, route steer requests through team-lead.",
        "when_not_to": "Not an invocation primitive by itself; it is the authorization signal for turn_steer.",
        "failure_modes": ["PEER_STEER_REJECTED", "STEER_AUTH_REJECTED"],
        "callable_from_peers": False,
    },
    "native_swarm": {
        "version": "1",
        "schema": {"type": "object", "additionalProperties": True},
        "description": "Use the harness's native internal multi-agent or swarm features inside one teammate process.",
        "when_to_use": "Route broad exploration or parallelizable research to a teammate that can fan out internally.",
        "when_not_to": "Do not assume internal swarm activity is visible as separate team members or mailbox participants.",
        "failure_modes": ["SWARM_UNAVAILABLE", "SUBAGENT_LIMIT_REACHED", "SWARM_OUTPUT_COLLAPSED"],
        "callable_from_peers": False,
    },
}


def assert_known_capabilities(capabilities: list[str]) -> list[str]:
    """Return a copy after asserting adapter-declared flags use the taxonomy."""
    unknown = sorted(set(capabilities) - CAPABILITY_NAMES)
    if unknown:
        raise ValueError(f"unknown capability flag(s): {', '.join(unknown)}")
    return list(capabilities)


def rich_capability_manifest(
    capabilities: list[str],
    *,
    delivery_mode: str | None = None,
    expiry_semantics: str | None = None,
    steer_authorization: str | None = None,
    host_tool_surface: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return R12 rich entries for the supplied R11 cheap capability list.

    The keys intentionally align one-for-one with ``CAPABILITY_NAMES`` and the
    per-backend ``*_CAPABILITIES`` constants so 09 R12 never invents a second
    naming layer on top of codex-impl-cap's R11 surface.
    """
    assert_known_capabilities(capabilities)
    result: dict[str, dict[str, Any]] = {}
    for name in capabilities:
        entry = deepcopy(_BASE_CAPABILITY_MANIFEST[name])
        if name == "turn_steer":
            if delivery_mode:
                entry["delivery_mode"] = delivery_mode
            if expiry_semantics:
                entry["expiry_semantics"] = expiry_semantics
            if steer_authorization:
                entry["authorization"] = steer_authorization
            entry["callable_from_peers"] = steer_authorization == "any_peer"
        if name == "live_tool_events" and host_tool_surface:
            entry["host_tool_surface"] = host_tool_surface
        result[name] = entry
    return result


def build_agent_card(
    *,
    team_name: str,
    agent_name: str,
    agent_id: str,
    agent_type: str,
    model: str,
    backend_type: str,
    capabilities: list[str],
    capability_manifest: dict[str, dict[str, Any]] | None = None,
    capability_version: str = CAPABILITY_MANIFEST_VERSION,
    transport: str | None = None,
    host_tool_surface: str | None = None,
) -> dict[str, Any]:
    """Build the rich R12 Agent Card persisted under ``manifests/<agent>.json``."""
    entries = capability_manifest if capability_manifest is not None else rich_capability_manifest(capabilities)
    return {
        "schema_version": CAPABILITY_MANIFEST_SCHEMA_VERSION,
        "capability_version": str(capability_version),
        "team_name": team_name,
        "agent_name": agent_name,
        "agent_id": agent_id,
        "agent_type": agent_type,
        "model": model,
        "backend_type": backend_type,
        "transport": transport or backend_type,
        "host_tool_surface": host_tool_surface,
        "capabilities": entries,
    }
