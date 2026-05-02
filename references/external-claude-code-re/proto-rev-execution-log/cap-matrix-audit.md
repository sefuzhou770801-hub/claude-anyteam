# Capability manifest cross-backend completeness audit

Date: 2026-04-28  
Task: #46  
Base: `b0e226a` (`tests: cover app-server transport reconnect recovery`)

## Scope and method

Read the current capability taxonomy in `src/claude_anyteam/capabilities.py` and compared the declared per-backend cheap flags plus rich Agent Card entries against the actual runtime surfaces in:

- Codex: `src/claude_anyteam/loop.py`, `src/claude_anyteam/codex.py`, `src/claude_anyteam/app_server.py`
- Gemini: `src/claude_anyteam/backends/gemini/loop.py`, `invoke.py`, `acp.py`, `acp_client.py`
- Kimi: `src/claude_anyteam/backends/kimi/loop.py`, `invoke.py`, `config.py`
- Kimi reality check docs: `docs/internal/kimi-integration/kimi-runtime.md` and `kimi-skill-and-agent-research.md`

North-star lens from `CLAUDE.md` §1: each backend's unique harness primitives must be surfaced; the protocol should carry capability declarations rather than flattening to the common subset.

## Current declared matrix at `b0e226a`

| Backend | Current declared flags | Immediate issue |
| --- | --- | --- |
| `codex-app-server` | `turn_steer`, `thread_fork`, `live_tool_events`, `structured_output`, `soft_non_progress_watchdog` | Mostly aligned, but App Server transport recovery / interrupt semantics are only implicit. |
| `codex-exec` | `structured_output` | Under-declared: resume/session continuity, headless invocation shape, output-last-message, and bypass semantics exist in code. |
| `gemini-acp` | `turn_steer`, `permission_bridge`, `live_tool_events`, `accepts_peer_steer` | Under-declared: structured task/plan output, plan mode, session load/persistence, trust modes, blocking ACP session prompt. Also rich manifest overstates steer delivery as `live`. |
| `gemini-headless` | none | Under-declared: headless stream-json invocation, session resume, structured task/plan output, plan mode. |
| `kimi-headless` | `large_context` | Under-declared: headless print/stream-json invocation, session resume, structured task/plan output, plan mode, and Kimi-native skill discovery. |

## Backend findings

### Codex App Server

**Confirmed present and correctly declared:**

- `turn_steer`: `_execute_task_app_server` drains in-flight inbox messages and pushes accepted steer text into `SteerQueue`; `app_server_invoke` submits queued text through `AppServerClient.turn_steer`.
- `thread_fork`: `_start_or_fork_thread` uses `thread/fork` for the next task when the prior App Server thread is materialized.
- `live_tool_events`: App Server native items are normalized into visibility envelopes via `_visibility_for_app_server_item`.
- `structured_output`: `turn/start` receives inline `outputSchema`; completion parses the final `agentMessage` as JSON.
- `soft_non_progress_watchdog`: App Server event loop warns and optionally interrupts when no visible checkpoint arrives.

**Additional actual App Server surfaces:**

- **Wrapper MCP inside App Server**: the adapter injects `config.mcp_servers.claude_anyteam_wrapper` in `thread/start` so Codex can call `send_message`, `task_update`, shadow shell/file tools, etc. Identity is passed in wrapper args rather than env because App Server did not forward adapter env in prior live testing.
- **Native host tool surface**: `thread/start` uses `sandbox="danger-full-access"` and `approval_policy="never"`; host shell/file activity remains Codex-native and is only observed/normalized by the adapter.
- **Transport recovery**: `AppServerClient.reconnect_and_resume` and `app_server_invoke` recover from a lost App Server transport by restarting the child, calling `thread/resume`, and either salvaging a terminal turn or starting a recovery continuation turn.
- **Turn interrupt**: `turn/interrupt` is used for wall-clock timeout cleanup and optional non-progress hard interrupt.

**Proposal:** keep the existing App Server declarations, but add an informational capability for session/transport recovery only if peers need to route based on it. For this task's fix, the more urgent gaps are cross-backend structured output / session-resume / plan/trust/Kimi-native declarations.

Implementation note for the follow-up fix: `plan_mode` is declared for Codex App Server teammates too. Although ordinary task turns use App Server when enabled, the current plan-approval handler is a transport-independent teammate primitive and generates the structured plan through the Codex CLI path.

### Codex exec

**Actual code:**

- Fresh path runs `codex exec --json --output-last-message <file> --output-schema <schema> -C <cwd>`.
- Resume path runs `codex exec resume <session_id> --json --output-last-message <file>` and deliberately omits `--output-schema` and `-C` because Codex CLI 0.122 rejected them on resume.
- Both paths hardcode `--dangerously-bypass-approvals-and-sandbox`.
- The loop captures `thread.started.thread_id` and validates resumed task-complete JSON in Python when CLI schema support is unavailable.

**Gaps:** `CODEX_EXEC_CAPABILITIES` only declares `structured_output`. It should also declare the session-resume/headless-exec primitive; a Codex exec teammate is not merely a stateless schema-output runner.

### Gemini ACP

**Actual code:**

- Starts `gemini --acp` / `--experimental-acp` via `GeminiAcpClient`.
- Initializes with ACP protocol v1 and session methods.
- Loads an existing session using `session/load` before falling back to `session/new`.
- Runs a blocking `session/prompt` call, then drains notifications through `drain_notifications()`.
- Sets trust mode through `session/set_mode` using `trusted -> yolo`, `default -> default`, `plan -> plan`.
- Bridges `session/request_permission` to team-lead when trust mode is not `trusted`, waits for `permission_response`, and maps allow/deny outcomes back to ACP options.
- Parses final assistant text into task-complete / plan JSON with Python schema validation.

**Gaps:**

- `structured_output` is missing even though Gemini ACP task completion and plan generation are schema-validated.
- `plan_mode` is missing even though `_handle_plan_approval` supports structured plan approval.
- `session_resume` is missing even though `session/load` and adapter-state persistence are core ACP behavior.
- `trust_modes` is missing even though default/plan/trusted materially change permission behavior and peer routing expectations.
- The rich manifest currently says Gemini ACP `turn_steer.delivery_mode="live"`; the code's `session_prompt` call blocks and the loop injects queued steers at the next prompt boundary. That should be corrected to a non-live delivery mode (for example `next_turn`) unless a true mid-turn ACP steer/cancel/re-prompt path lands later.

### Gemini headless

**Actual code:**

- Runs `gemini --prompt <prompt> --output-format stream-json --approval-mode yolo`.
- Optionally resumes with `--resume <session_id>`.
- Parses `init.session_id`, `message`, `tool_use`, and terminal `result` events.
- Uses Python schema validation for task and plan outputs.
- Prose/plan turns are explicitly ephemeral from the loop's point of view; task turns can still carry session continuity.

**Gaps:** no capabilities are declared. Even if the legacy headless transport is intentionally looser than ACP, it should advertise `headless_invocation`, `session_resume`, `structured_output`, and `plan_mode` (not `live_tool_events`, because visibility is terminal/normalized rather than live mid-turn).

### Kimi headless

**Actual code and reality check:**

- Runs `kimi --print --output-format=stream-json --work-dir <cwd> --mcp-config-file <file> -p <prompt>`.
- Uses an isolated Kimi HOME and adapter-owned MCP config with bare wrapper tool names.
- Uses `--session <id>` only when the session directory is known, avoiding Kimi's documented silent-create-on-miss behavior.
- Parses Kimi per-message NDJSON, assistant `tool_calls`, final assistant text, and stderr resume hints.
- Uses Python schema validation for task and plan outputs.
- Kimi runtime docs record `merge_all_available_skills = true` and the v1 decision: do not pass `--skills-dir` by default; let Kimi discover native project/user/built-in skills itself. Internal subagents/swarm remain Kimi-private implementation details for v1, not anyteam-visible teammates.

**Gaps:** `KIMI_HEADLESS_CAPABILITIES` declares only `large_context`. It should also advertise headless invocation, session resume, structured output, plan mode, and Kimi native skill discovery. I do **not** recommend restoring the old `native_swarm` name as-is: v1 does not expose Kimi subagents as anyteam peers and does not provide stable list/invoke semantics. If we advertise this area, the safer declaration is non-callable `native_skills` (and later, a separate explicit `internal_subagents` if we add tests/logging around Kimi's `Agent` tool).

## Proposed fix set

1. Add missing non-decorative capability names:
   - `headless_invocation`
   - `session_resume`
   - `plan_mode`
   - `trust_modes`
   - `native_skills`
2. Add them to backend declarations:
   - `codex-app-server`: add `plan_mode` in addition to the existing App Server flags.
   - `codex-exec`: `headless_invocation`, `session_resume`, `plan_mode` in addition to `structured_output`.
   - `gemini-acp`: `structured_output`, `plan_mode`, `session_resume`, `trust_modes` in addition to existing ACP flags.
   - `gemini-headless`: `headless_invocation`, `session_resume`, `structured_output`, `plan_mode`.
   - `kimi-headless`: `headless_invocation`, `session_resume`, `structured_output`, `plan_mode`, `native_skills`, `large_context`.
   - Keep `codex-app-server` as-is for now; optionally add a future `transport_recovery` capability if peers start routing based on recovery behavior.
3. Add hook registry entries and schema entries for each new flag.
4. Add tests that the matrix reflects the audited capabilities, that Gemini ACP's rich steer delivery is not advertised as live, and that Kimi native skills are preserved by default by not emitting `--skills-dir`.
