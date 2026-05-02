"""Control loop for native Claude Code headless teammates.

This is intentionally a narrow bridge: protocol I/O is handled by the adapter
so detached stress runs can observe tasks and visibility events, while task
execution is delegated to the native `claude --print` CLI rather than another
LLM wrapper.
"""
from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass, field
from typing import Any

from claude_anyteam import logger, protocol_io as pio
from claude_anyteam.capability_manifest import CapabilityManifestCache
from claude_anyteam.capabilities import (
    CLAUDE_NATIVE_HEADLESS_CAPABILITIES,
    assert_known_capabilities,
    rich_capability_manifest,
)
from claude_anyteam.codex import TASK_COMPLETE_SCHEMA
from claude_anyteam.messages import CapabilityManifestUpdatedIn, ShutdownRequestIn, parse_protocol_text
from claude_anyteam.registration import BackendMetadata, deregister, register
from claude_anyteam.schema_validation import inline_schema_prompt_fragment, load_schema
from claude_anyteam.watch_inbox import WatchInbox, adaptive_wait_s

from . import invoke, prompts
from .config import ClaudeNativeSettings


@dataclass
class ClaudeNativeLoopState:
    settings: ClaudeNativeSettings
    shutdown_requested: bool = False
    approved_shutdown: bool = False
    in_flight_task: str | None = None
    seen_shutdown_request_ids: set[str] = field(default_factory=set)
    claude_session_id: str | None = None
    peer_manifest_cache: CapabilityManifestCache | None = None
    self_capability_manifest: dict[str, Any] | None = None


def _backend_metadata(settings: ClaudeNativeSettings) -> BackendMetadata:
    capabilities = assert_known_capabilities(CLAUDE_NATIVE_HEADLESS_CAPABILITIES)
    host_tool_surface = "claude-code-native(Task,Skill,WebFetch,Read,Edit,Write,Bash)+mcp_anyteam"
    return BackendMetadata(
        agent_type="claude",
        model=settings.model or "sonnet",
        prompt=(
            "Native Claude Code teammate bridge. Protocol I/O is supervised by "
            "claude-anyteam so detached stress runs have visibility; coding work "
            "is delegated to the host Claude Code CLI."
        ),
        backend_type="claude_native",
        capabilities=capabilities,
        capability_manifest=rich_capability_manifest(
            capabilities,
            host_tool_surface=host_tool_surface,
        ),
        transport="claude-native-headless",
        host_tool_surface=host_tool_surface,
        coupling_regime="loose",
    )


def run(settings: ClaudeNativeSettings) -> int:
    state: ClaudeNativeLoopState | None = None
    exit_code = 0
    startup_phase = "feature_test"
    startup_complete = False
    try:
        invoke.feature_test(settings.claude_binary)
        startup_phase = "registration"
        register(settings, _backend_metadata(settings))
        startup_phase = "capability_manifest_load"
        state = ClaudeNativeLoopState(settings=settings)
        state.self_capability_manifest = pio.read_agent_manifest(settings.team_name, settings.agent_name)
        state.peer_manifest_cache = CapabilityManifestCache(settings.team_name, self_name=settings.agent_name)
        state.peer_manifest_cache.load_startup()

        def _sig_handler(signum: int, _frame: Any) -> None:
            logger.warn("claude_native.signal.received", signum=signum)
            assert state is not None
            state.shutdown_requested = True

        startup_phase = "signal_setup"
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
        startup_complete = True
        _main_loop(state)
    except Exception as e:
        if startup_complete:
            logger.error("claude_native.loop.crash", error=str(e))
            error_class = "adapter_crash"
        else:
            logger.error("claude_native.startup.crash", phase=startup_phase, error=str(e))
            error_class = "adapter_startup_crash"
            try:
                pio.emit_adapter_startup_crash(
                    team=settings.team_name,
                    agent=settings.agent_name,
                    backend="claude_native",
                    phase=startup_phase,
                    error=e,
                    payload={
                        "claude_binary": settings.claude_binary,
                        "cwd": str(settings.cwd),
                        "model": settings.model,
                        "effort": settings.effort,
                    },
                )
            except Exception as emit_exc:
                logger.debug("claude_native.startup.visibility_emit_failed", error=str(emit_exc))
        try:
            from claude_anyteam import diagnostics as _diag
            _diag.record_incident(
                team=settings.team_name,
                agent=settings.agent_name,
                backend="claude_native",
                error_class=error_class,
                summary=str(e),
            )
        except Exception:
            pass
        exit_code = 1
    finally:
        if state is not None and state.approved_shutdown:
            deregister(settings)
            logger.info("claude_native.loop.deregistered", name=settings.agent_name)
        else:
            logger.warn(
                "claude_native.loop.exit_without_deregister",
                in_flight_task=(state.in_flight_task if state is not None else None),
            )
    return exit_code


def _main_loop(state: ClaudeNativeLoopState) -> None:
    s = state.settings
    logger.info("claude_native.loop.start", team=s.team_name, name=s.agent_name, poll_s=s.poll_interval_s)
    idle_last_sent_at: float | None = None
    inbox_watch = WatchInbox.for_team(s.team_name, s.agent_name, fallback_timeout_s=s.poll_interval_s)
    try:
        while not state.approved_shutdown:
            messages = pio.read_own_inbox(s.team_name, s.agent_name, s.agent_name)
            saw_messages = bool(messages)
            for msg in messages:
                _handle_message(state, msg)
                if state.approved_shutdown:
                    return

            if not state.shutdown_requested:
                claimed = _find_and_claim(state)
                if claimed is not None:
                    _execute_task(state, claimed)
                    idle_last_sent_at = None
                    continue

            if not _has_claimable(state):
                now = time.monotonic()
                if idle_last_sent_at is None or (now - idle_last_sent_at) > 60:
                    try:
                        pio.send_idle_notification(s.team_name, s.agent_name)
                        idle_last_sent_at = now
                        logger.info("claude_native.idle.sent")
                    except Exception as e:
                        logger.warn("claude_native.idle.send_fail", error=str(e))
            inbox_watch.wait_for_change(adaptive_wait_s(saw_messages=saw_messages))
            if state.shutdown_requested and state.in_flight_task is None:
                state.claude_session_id = None
                state.approved_shutdown = True
                return
    finally:
        inbox_watch.close()


def _handle_message(state: ClaudeNativeLoopState, msg: Any) -> None:
    payload = parse_protocol_text(msg.text)
    if isinstance(payload, ShutdownRequestIn):
        _handle_shutdown(state, payload)
        return
    if isinstance(payload, CapabilityManifestUpdatedIn):
        if state.peer_manifest_cache is not None:
            state.peer_manifest_cache.apply_update(payload)
        logger.info(
            "claude_native.capability_manifest.update_seen",
            agent=payload.agent_name,
            capability_version=payload.capability_version,
            removed=payload.removed,
        )
        return
    if payload is None:
        _handle_prose(state, msg)
    else:
        logger.debug("claude_native.inbox.protocol_noop", type=payload.__class__.__name__)


def _peer_prompt_fragments(state: ClaudeNativeLoopState) -> str:
    if os.environ.get("CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS") == "1":
        return ""
    if state.peer_manifest_cache is None:
        return ""
    try:
        return state.peer_manifest_cache.peer_prompt_fragments_for(state.settings.agent_name)
    except Exception as e:
        logger.warn("claude_native.capability_manifest.peer_prompt_fragments_fail", error=str(e))
        return ""


def _prose_visibility_event_sink(state: ClaudeNativeLoopState):
    s = state.settings

    def _sink(event) -> None:
        try:
            pio.append_event(s.team_name, s.agent_name, event)
        except Exception as e:
            logger.warn("claude_native.visibility.append_fail", kind=getattr(event, "kind", None), error=str(e), surface="prose")
        visibility = getattr(event, "visibility", None)
        if getattr(visibility, "mailbox", False):
            try:
                pio.send_visibility_event_to_lead(s.team_name, s.agent_name, event, summary=event.summary[:120])
            except Exception as e:
                logger.warn("claude_native.visibility.mailbox_fail", kind=getattr(event, "kind", None), error=str(e), surface="prose")

    return _sink


def _handle_prose(state: ClaudeNativeLoopState, msg: Any) -> None:
    s = state.settings
    sender = getattr(msg, "from_", "unknown")
    logger.info("claude_native.inbox.prose", sender=sender, summary=getattr(msg, "summary", None))
    prompt = prompts.prose_reply_prompt(
        sender=sender,
        body=msg.text,
        agent_name=s.agent_name,
        team_name=s.team_name,
        peer_prompt_fragments=_peer_prompt_fragments(state),
    )
    result = None
    try:
        result = invoke.run(
            prompt,
            cwd=s.cwd,
            schema=None,
            claude_binary=s.claude_binary,
            timeout_s=s.turn_timeout_s,
            wrapper_identity=(s.team_name, s.agent_name),
            model=s.model,
            effort=s.effort,
            event_sink=_prose_visibility_event_sink(state),
        )
    except Exception as e:
        logger.warn("claude_native.prose.crash", sender=sender, error=str(e))
    if result is not None and pio.should_skip_prose_fallback(result):
        logger.info("claude_native.prose.delivered_via_tool", sender=sender, tool_calls=getattr(result, "tool_call_events", 0))
        return
    reply = result.last_message if result is not None and result.exit_code == 0 and result.last_message else "Acknowledged."
    try:
        pio.send_prose(s.team_name, s.agent_name, sender, reply, summary="prose_reply")
        logger.info("claude_native.prose.reply_sent", sender=sender)
    except Exception as e:
        logger.warn("claude_native.prose.reply_send_fail", sender=sender, error=str(e))


def _handle_shutdown(state: ClaudeNativeLoopState, payload: ShutdownRequestIn) -> None:
    s = state.settings
    req_id = payload.effective_request_id() or "shutdown-unknown"
    if req_id in state.seen_shutdown_request_ids:
        return
    state.seen_shutdown_request_ids.add(req_id)
    if state.in_flight_task is not None:
        reason = f"in-flight task #{state.in_flight_task}"
        logger.info("claude_native.shutdown.reject", request_id=req_id, in_flight=state.in_flight_task)
        try:
            pio.send_shutdown_rejected(s.team_name, s.agent_name, req_id, reason=reason)
        except Exception as e:
            logger.warn("claude_native.shutdown.response_fail", error=str(e))
        state.shutdown_requested = True
        return
    logger.info("claude_native.shutdown.approve", request_id=req_id)
    try:
        pio.send_shutdown_approved(s.team_name, s.agent_name, req_id)
    except Exception as e:
        logger.warn("claude_native.shutdown.response_fail", error=str(e))
    state.claude_session_id = None
    state.approved_shutdown = True


def _find_and_claim(state: ClaudeNativeLoopState):
    s = state.settings
    try:
        all_tasks = pio.list_tasks(s.team_name)
    except Exception as e:
        logger.warn("claude_native.tasks.list_fail", error=str(e))
        return None
    candidates = [t for t in all_tasks if t.status == "pending" and not _blocked(all_tasks, t) and t.owner == s.agent_name]
    candidates += [t for t in all_tasks if t.status == "pending" and not _blocked(all_tasks, t) and t.owner in (None, "")]
    for t in sorted(candidates, key=lambda x: int(x.id)):
        try:
            claimed = pio.claim_task(s.team_name, t.id, s.agent_name, active_form=f"Running native Claude on task #{t.id}")
            state.in_flight_task = claimed.id
            logger.info("claude_native.task.claimed", task_id=claimed.id, subject=claimed.subject)
            return claimed
        except ValueError:
            continue
    return None


def _has_claimable(state: ClaudeNativeLoopState) -> bool:
    try:
        all_tasks = pio.list_tasks(state.settings.team_name)
    except Exception:
        return False
    return any(
        t.status == "pending"
        and not _blocked(all_tasks, t)
        and t.owner in (None, "", state.settings.agent_name)
        for t in all_tasks
    )


def _blocked(all_tasks: list, t) -> bool:
    if not getattr(t, "blocked_by", None):
        return False
    by_id = {x.id: x for x in all_tasks}
    return any((by_id.get(bid) is not None and by_id[bid].status not in ("completed", "deleted")) for bid in t.blocked_by)


def _execute_task(state: ClaudeNativeLoopState, task) -> None:
    s = state.settings
    schema = load_schema(TASK_COMPLETE_SCHEMA)
    result = None
    for attempt in (1, 2):
        prompt = prompts.task_prompt(
            task,
            agent_name=s.agent_name,
            team_name=s.team_name,
            peer_prompt_fragments=_peer_prompt_fragments(state),
        )
        prompt += "\n\n# Output contract\n" + inline_schema_prompt_fragment(schema)
        if attempt == 2:
            prompt += "\n\nPRIOR ATTEMPT FAILED: return ONLY the JSON object matching the schema."
        try:
            result = invoke.run(
                prompt,
                cwd=s.cwd,
                schema=TASK_COMPLETE_SCHEMA,
                claude_binary=s.claude_binary,
                timeout_s=s.turn_timeout_s,
                wrapper_identity=(s.team_name, s.agent_name),
                # Fresh sessions avoid Claude Code --resume edge cases in
                # detached stress mode; repository state is the durable memory.
                resume_session_id=None,
                model=s.model,
                effort=s.effort,
                task_id=str(task.id),
            )
        except Exception as exc:
            logger.warn("claude_native.task.crash", task_id=task.id, attempt=attempt, error=str(exc))
            _mark_blocked(state, task, reason=f"Claude invocation crashed: {exc}")
            state.in_flight_task = None
            return
        if result.exit_code == 0 and result.structured is not None:
            if result.session_id:
                state.claude_session_id = result.session_id
            break
    if result is None or result.exit_code != 0 or result.structured is None:
        _mark_blocked(state, task, result.error if result else "Claude invocation did not run")
        state.in_flight_task = None
        return
    files_changed = result.structured.get("files_changed") or []
    summary_text = result.structured.get("summary") or "(no summary)"
    try:
        pio.update_task(s.team_name, task.id, status="completed")
    except Exception as e:
        logger.warn("claude_native.task.complete_fail", task_id=task.id, error=str(e))
        state.in_flight_task = None
        return
    try:
        pio.send_task_complete(s.team_name, s.agent_name, task_id=task.id, files_changed=files_changed, summary_text=summary_text, codex_exit_code=result.exit_code)
    except Exception as e:
        logger.warn("claude_native.task.complete_msg_fail", task_id=task.id, error=str(e))
    logger.info("claude_native.task.completed", task_id=task.id, files=len(files_changed))
    state.in_flight_task = None


def _mark_blocked(state: ClaudeNativeLoopState, task, reason: str) -> None:
    s = state.settings
    try:
        current = pio.get_task(s.team_name, task.id)
        if getattr(current, "status", None) == "completed":
            logger.info("claude_native.task.block_skip_already_completed", task_id=task.id, reason_would_have_been=reason[:120])
            return
    except Exception as e:
        logger.warn("claude_native.task.block_precheck_fail", task_id=task.id, error=str(e))
    try:
        pio.update_task(
            s.team_name,
            task.id,
            active_form=f"blocked: {reason[:80]}",
            metadata={"blocked_reason": reason, "blocked_by": s.agent_name},
        )
    except Exception as e:
        logger.warn("claude_native.task.block_update_fail", task_id=task.id, error=str(e))
    try:
        pio.send_task_blocked(s.team_name, s.agent_name, task_id=task.id, reason=reason)
    except Exception as e:
        logger.warn("claude_native.task.block_msg_fail", task_id=task.id, error=str(e))
