# aproto-codex-bridge — research digest

## What is it

`aproto9787/codex-bridge` is a Node.js bridge that makes `codex-*` workers appear as Claude Code team teammates by installing itself as `CLAUDE_CODE_TEAMMATE_COMMAND` and routing spawned workers by name prefix. It wraps Codex App Server directly, uses Claude Code's on-disk team mailboxes/tasks (`~/.claude/teams`, `~/.claude/tasks`) for IPC, and uses a shell CLI (`codex-bridge send ...`) for Codex-originated team messages rather than exposing an MCP wrapper tool.

## Bridge architecture

The bridge is a single executable module (`codex-bridge.mjs`, 3107 LOC) plus an optional Ink viewer (`codex-ink-viewer.mjs`, 543 LOC). It is **not MCP transport** for the team protocol; it is a Claude teammate-command shim/process wrapper that talks to Codex App Server by JSON-RPC over stdio or WebSocket (`codex-bridge.mjs:1765-1989`) and talks to Agent Teams by direct file I/O against inbox JSON files (`codex-bridge.mjs:1278-1337`). Routing is name/prefix based: `codex-*` runs Codex, `claude-*` or Claude-looking model names pass through to the real Claude CLI (`codex-bridge.mjs:2939-3002`, `3004-3029`; README `:136-143`).

The Codex worker gets base instructions from `teamagent.md`, plus a line listing available teammates and the team `config.json` description as goal context (`codex-bridge.mjs:45-75`; README `:62-65`, `:271-273`). The bridge starts Codex App Server with `approvalPolicy: "never"`, `sandbox: "danger-full-access"`, current `cwd`, and those base instructions (`codex-bridge.mjs:1938-1957`); model selection is deliberately minimal/fixed around `codexEffort = "xhigh"` (`codex-bridge.mjs:1711-1714`), with no rich per-worker capability/model declaration layer.

Codex's **team-protocol exposure** is a shell command, not a model-visible MCP tool: `codex-bridge send <target> [--summary ...] [--file ...] "<message>"` (`teamagent.md:30-83`; README `:223-239`). `handleSendCommand` writes directly to target inbox files (`codex-bridge.mjs:1575-1603`), including broadcast expansion through the roster (`codex-bridge.mjs:1439-1447`). Bridge-generated outbound messages go through `MessageRouter`, a per-target in-memory queue with 50ms micro-batching and CAS+rename fallback to reduce lock hold time and file churn (`codex-bridge.mjs:1339-1426`).

Inbound messages are claimed and marked read atomically in one lock/parse/write cycle (`codex-bridge.mjs:1302-1327`). The main loop is event-driven when possible: it installs `fs.watch` on the inbox directory (`codex-bridge.mjs:2554-2577`), wakes on changes with a 100ms active timeout and 5s fallback (`codex-bridge.mjs:14-18`, `2858-2868`), and falls back to adaptive polling if `fs.watch` fails (`codex-bridge.mjs:2862-2868`). If a message arrives while Codex is already in a turn, the bridge injects it via `turn/steer`; failed/raced steers are queued for the next turn (`codex-bridge.mjs:2379-2397`, `2591-2602`, `2801-2825`, `2638-2649`).

Error semantics are pragmatic and file/message oriented: Codex RPC timeout is 30s per request (`codex-bridge.mjs:2170-2201`), turn errors resolve to failed outputs (`codex-bridge.mjs:2330-2359`), app-server fatal/close rejects in-flight work (`codex-bridge.mjs:2362-2377`), and task failures are written to `results/` plus previewed to team-lead (`codex-bridge.mjs:2686-2700`, `2704-2737`). It has strong process hardening for TUI/WebSocket crashes: unexpected WebSocket close triggers one restart on a fresh port with SIGTERM→SIGKILL escalation (`codex-bridge.mjs:1901-1920`, `2081-2131`), and native TUI crashes are handled differently depending on whether the WebSocket is still alive (`codex-bridge.mjs:2518-2527`). On shutdown, it sends `shutdown_approved`, removes itself from `config.json`, closes viewers, and kills its tmux pane (`codex-bridge.mjs:1632-1652`, `1655-1669`, `2894-2935`).

## Direct comparison vs. claude-anyteam

| Surface | aproto-codex-bridge | claude-anyteam | Net |
|---|---|---|---|
| Spawn entrypoint | Replaces Claude Code teammate command via `CLAUDE_CODE_TEAMMATE_COMMAND`; routes every spawn through one Node script (`README:91-103`, `codex-bridge.mjs:3004-3029`). | Uses spawn shim / adapter processes registered into existing team; per-backend CLIs and wrapper MCP. | Same goal, different insertion point. Their path is closer to native TeamCreate spawn interception; ours is less invasive to Claude's spawn command but more self-registering. |
| Transport to Codex | Codex App Server JSON-RPC over stdio or WebSocket (`codex-bridge.mjs:1765-1989`). | Codex App Server client over stdio (`src/claude_anyteam/app_server.py:14-31`) and explicit `thread/start`, `turn/start`, `turn/steer`, `thread/fork` helpers (`app_server.py:41-185`). | Same primitive family. They add WebSocket/native TUI mode; we add explicit thread/fork lineage. |
| Team protocol transport | Direct file I/O to inbox/task/config JSON, with own locks/router (`codex-bridge.mjs:99-127`, `1278-1426`). | Delegates to shared `claude_teams` substrate through `protocol_io` and wrapper MCP; wrapper delegates to `claude_teams` (`wrapper_server.py:15-20`, `49-53`). | Same on-disk protocol; they own more custom file code, ours centralizes/validates. |
| Codex-visible team tools | Shell CLI instruction: `codex-bridge send ...` (`teamagent.md:30-83`). No MCP tool surface for task_update/read_inbox/capability lookup. | Narrow MCP server exposes `send_message`, `task_update`, `task_create`, `read_inbox`, `task_list`, `read_config`, `mcp_anyteam_capability_manifest` plus shadow tools (`wrapper_server.py:103-120`, `382-393`, `732-1000`). | Our wrapper-MCP is materially stronger for model tool discovery, typed errors, and parity with native `SendMessage`. Their CLI is simpler and works through Codex's native shell. |
| Capability declaration | None beyond naming rules, base instructions, env vars, and roster line (`codex-bridge.mjs:55-64`; `teamagent.md:93-100`). | Cheap flags on `members[].capabilities`, rich Agent Card manifests under `manifests/<agent>.json`, startup cache, update broadcasts, MCP lookup (`capabilities.py:16-39`, `capability_manifest.py:1-8`, `111-132`, `260-341`, `registration.py:183-239`). | We are ahead. They do not teach peers about unique primitives beyond prompt text. |
| Peer DM | `codex-bridge send <worker>` writes directly to that worker's inbox; broadcast supports `*` (`README:231-237`, `teamagent.md:79-83`). | `send_message(to=peer, body=..., kind=...)` via wrapper MCP, with membership validation and messageKind stamping (`wrapper_server.py:803-894`). | Same basic capability. Their inbox watcher should reduce wake latency; our typed `kind` avoids steer/DM confusion. |
| Peer steer | Any inbound message during active turn becomes `turn/steer` with a hardcoded leader-flavored prefix (`codex-bridge.mjs:2385-2395`, `2801-2819`). | Lead prose and explicitly accepted peer steer route through `SteerQueue`, with manifest/authorization gates and informational DMs deferred (`loop.py:1242-1306`; `wrapper_server.py:566-634`, `842-863`). | Their delivery path is faster/simpler; ours is safer and §3-correct for peer-DM vs steer semantics. |
| Visibility | Live ANSI/Ink/native TUI rendering from App Server notifications, plus `[STATUS]`, `[ERROR]`, `idle_notification`, result previews in leader inbox (`codex-bridge.mjs:460-817`, `2475-2549`, `2632-2700`). | Structured visibility events to stderr/event log/mailbox/task-state projections, host-tool event classifier, watchdog, wrapper tool instrumentation (`codex.py:80-112`, `1027-1115`, `1203-1380`, `1434-1465`; `wrapper_server.py:407-430`, `658-725`). | They win on interactive terminal UX/native TUI recovery; we win on structured, queryable lead visibility. |
| Result size/context | Full output to `results/`, inbox gets 500-char preview (`codex-bridge.mjs:20-21`, `1723-1744`, `2660-2670`). | Task-complete schema keeps coding results concise; visibility/diagnostics artifacts exist, but prose/DM long-output previewing is less universally codified. | Liftable pattern: general two-stage mailbox payloads. |
| Task lifecycle | Auto-marks all own `in_progress` tasks completed after a successful turn (`codex-bridge.mjs:1681-1709`, `2672-2685`). | Explicit `task_update`, claim/update guards, schema-validated task completion, no blind all-owned completion (`wrapper_server.py:898-947`; `loop.py:112-156`, `loop.py:915-947`). | Our task lifecycle is safer. Their auto-complete ordering prevents reschedule races but is too broad. |
| Process model | Long-lived Node worker, one Codex App Server session, optional native TUI in same pane; Claude passthrough is child process (`codex-bridge.mjs:1765-1989`, `2466-2888`, `2939-3002`). | Long-lived Python adapter loop, Codex App Server per task with persisted/forked lineage; wrappers as MCP subprocesses. | They preserve one continuous thread naturally; we isolate tasks with explicit `thread/fork` lineage. |

Surfaces they have that we do not have in equivalent form:

- `fs.watch` inbox wakeups and adaptive active/fallback polling (`codex-bridge.mjs:2554-2587`, `2858-2868`). Our main loops sleep fixed `poll_interval_s` (`loop.py:215-266`; default 1.5s in `config.py:112`).
- Per-target micro-batched MessageRouter with CAS+rename and 50ms debounce (`codex-bridge.mjs:1339-1426`). Our protocol writes generally append per message under filelock.
- Native Codex TUI/WebSocket path and recovery (`codex-bridge.mjs:1093-1241`, `1901-1920`, `2081-2131`, `2518-2527`). Our visibility is structured but not a native Codex terminal UI.
- Full-result file storage with inbox preview as a first-class invariant (`codex-bridge.mjs:1723-1744`). We have schema discipline/diagnostic artifacts, but not a universal mailbox preview/attachment protocol.

Surfaces we have that they do not:

- Narrowed MCP protocol surface visible to Codex (`wrapper_server.py:103-120`, `803-1000`), including task/task-list/read-inbox/read-config/capability-manifest tools.
- Rich capability declarations and cached Agent Card manifests (`capabilities.py:16-39`, `279-316`; `capability_manifest.py:111-132`, `285-341`; `registration.py:224-239`).
- Explicit `messageKind`/`kind` discrimination for informational vs steer vs handoff (`wrapper_server.py:62`, `803-823`, `887-894`; `loop.py:1258-1280`).
- Sender-side peer-steer manifest precondition with visibility-degraded feedback (`wrapper_server.py:566-634`, `847-863`).
- `thread/fork` cross-task lineage with materialization fallback (`app_server.py:126-185`; `codex.py:656-701`, `991-1006`).
- Structured visibility events and watchdog projections (`codex.py:1027-1115`, `1203-1380`, `1434-1465`; `wrapper_server.py:658-725`).

## §1 (harness preservation) comparison

The bridge preserves several Codex-native primitives well: it uses Codex App Server directly, starts real `turn/start` turns, injects mid-turn input via `turn/steer`, and can attach a native Codex TUI over WebSocket (`codex-bridge.mjs:1810-1823`, `1938-1957`, `2379-2397`, `1166-1241`). It also preserves Codex host-tool execution as Codex-native events; the renderer explicitly understands `commandExecution`, `fileChange`, `webSearch`, plan, diff, token usage, and error notifications (`codex-bridge.mjs:460-817`).

However, it flattens or hardcodes important harness policy knobs: `approvalPolicy` is always `never`, sandbox is always `danger-full-access`, and reasoning effort is fixed to `xhigh` (`codex-bridge.mjs:1711-1714`, `1938-1954`). It does not expose a capability layer declaring which worker supports `turn_steer`, native TUI, live tool events, or thread continuity; peers infer from `codex-*` naming and prompt text. It keeps a single App Server thread for a worker lifetime, which preserves continuity, but it does not expose explicit `thread/fork` as a peer-invokable capability and does not isolate one task lineage from the next (`codex-bridge.mjs:1793-1807`, `2379-2439`).

Compared with claude-anyteam, their §1 preservation is stronger at the **interactive Codex UX** layer (native TUI, one continuous app-server session) and weaker at the **protocol/capability declaration** layer. Our current App Server path explicitly declares `turn_steer`, `thread_fork`, `live_tool_events`, `structured_output`, and `soft_non_progress_watchdog` for Codex App Server (`capabilities.py:31-39`), threads future work through a materialized `thread/fork` decision (`codex.py:656-701`), and passes model/effort as per-teammate settings (`config.py:61-79`; `codex.py:1140-1158`). We should not copy their fixed model/sandbox/approval policy as a protocol default.

## §2 (visibility parity) comparison

`codex-bridge` has unusually good **local pane visibility**. Its renderer converts Codex App Server notifications into compact ANSI output, including command starts/completions, command output tail on failure, file changes, plan updates, diffs, token totals, and errors (`codex-bridge.mjs:427-817`). The optional Ink viewer tracks streamed agent text, command output, reasoning summaries, plans, diffs, tokens, and errors (`codex-ink-viewer.mjs:306-475`). Native TUI mode can attach to the same app-server thread and recover from TUI/WebSocket crashes (`codex-bridge.mjs:1166-1241`, `2081-2131`, `2518-2527`).

Lead-facing visibility is less structured. The lead gets status ACKs, throttled `[STATUS]` progress messages, errors, task result previews, and `idle_notification` JSON in the inbox (`codex-bridge.mjs:2475-2483`, `2546-2549`, `2632-2700`, `1608-1627`). Full output is stored under `~/.claude/teams/<team>/results/`, and only a 500-character preview enters the inbox (`codex-bridge.mjs:1723-1744`). But individual host tool events are not delivered as typed mailbox/event-log records to the lead; if the lead is not watching the worker pane or reading `/tmp/codex-bridge-<agent>.log`, the fine-grained activity is not operationally visible.

claude-anyteam is closer to §2's intended substrate: App Server notifications become structured `VisibilityEvent` envelopes with backend-native payloads, event-log/stderr/mailbox/task-state projections, and wrapper MCP tools emit started/completed/failed events (`codex.py:1027-1115`, `1434-1465`; `wrapper_server.py:407-430`, `658-725`). Our weakness relative to them is UI richness/native TUI recovery, not visibility data model. The best lift is a UI projection of our existing event stream, not replacing structured visibility with pane-only rendering.

## §3 (peer efficiency) comparison

Measurable/derivable latency constants in `codex-bridge`:

- Inbox active wake interval: `POLL_ACTIVE_MS = 100` (`codex-bridge.mjs:14-17`).
- `fs.watch` fallback timeout: `POLL_FALLBACK_MS = 5000` (`codex-bridge.mjs:17`, `2858-2861`).
- MessageRouter batch debounce: `FLUSH_DEBOUNCE_MS = 50` (`codex-bridge.mjs:18`, `1361-1371`).
- Pure idle repeat cooldown: 30s (`codex-bridge.mjs:1605-1616`).
- Status message cooldown while running: 3s (`codex-bridge.mjs:2475-2483`).

The headline §3 win is event-driven inbox wakeup. The worker does not wait for a fixed 1.5s sleep in the common case; it blocks on `fs.watch` and drains promptly, with a 100ms active wait after seeing traffic (`codex-bridge.mjs:2554-2587`, `2858-2868`). That is a concrete pattern we do not currently have in the shared loops: claude-anyteam's Codex/Kimi/Gemini loops drain then `time.sleep(s.poll_interval_s)` (`loop.py:215-266`; Kimi/Gemini analogous), with default 1.5s (`config.py:112`, `backends/kimi/config.py:55`, `backends/gemini/config.py:64`). I DM'd team-lead immediately with this lift.

Capability-discovery cost is almost zero because there is no capability discovery; workers get a static available-teammates line and can read `config.json` manually (`codex-bridge.mjs:55-64`; `teamagent.md:93-100`). That is cheap but not semantically rich. Our manifest cache is more expensive upfront but much more correct: startup loads all manifests (`capability_manifest.py:298-312`), update events refresh cache (`capability_manifest.py:135-177`, `313-333`), and peer prompts can include cached capability guidance (`capabilities.py:412-454`; `loop.py:337-355`). The bridge therefore wins on **mechanical wakeup RTT** but loses on **capability-aware routing**.

Peer-DM semantics are mixed. `codex-bridge send codex-test ...` is fast direct inbox write, and broadcast expansion is cheap (`codex-bridge.mjs:1575-1598`, `1439-1447`). But if a peer DM lands while the recipient is busy, it becomes a steer unconditionally (`codex-bridge.mjs:2801-2819`), using a prompt prefix that says "Real-time message from leader" (`codex-bridge.mjs:2391-2395`). That optimizes reactivity at the cost of semantic correctness: routine informational DMs can perturb an active turn. claude-anyteam's current `kind="informational"` default and explicit `kind="steer"` manifest-gated path are slower/stricter but safer for §3 coordination fidelity (`wrapper_server.py:803-823`, `842-863`; `loop.py:1258-1280`).

Idle/heartbeat is better distinguished than many prototypes: `idle_notification` is structured JSON embedded in a normal inbox message with summary (`codex-bridge.mjs:1473-1485`, `1608-1627`), and status vs idle have separate cooldowns. It still lacks a versioned `messageKind` field for all routine traffic; claude-anyteam's `messageKind` stamping on wrapper sends is more machine-filterable (`wrapper_server.py:647-655`, `887-894`).

## Patterns they got right that we should lift

1. **Event-driven inbox wakeup with fallback polling.** Their constants and watch loop are concrete: 100ms active, 5s fallback, `fs.watch` on inbox dir (`codex-bridge.mjs:14-18`, `2554-2587`, `2858-2868`). Target: introduce `WatchInbox`/`InboxWake` in `src/claude_anyteam/protocol_io.py` or a small `watcher.py`, then replace fixed sleeps in `src/claude_anyteam/loop.py:215-266`, `backends/kimi/loop.py:244-273`, and `backends/gemini/loop.py:292-320`. Keep `--poll-s` as fallback/max wait; use inotify/watchfiles when available.

2. **Per-target micro-batched inbox writer.** `MessageRouter` queues by target, flushes after 50ms, prepares temp JSON outside the lock, then CAS-renames under lock and falls back to full lock on mtime race (`codex-bridge.mjs:1339-1426`). Target: `src/claude_teams/messaging.py:148-180` or a new optional `MessageSpool` used by high-volume visibility/status senders. Do not use it for required immediate protocol replies unless `flush=True` is supported as they do (`codex-bridge.mjs:1544-1550`, `1561-1568`, `1626`).

3. **Two-stage result storage with preview in mailbox.** Full result is persisted under a team `results/` dir; inbox gets bounded preview plus path (`codex-bridge.mjs:1723-1744`, `2660-2670`). Target: extend `protocol_io.send_prose_to_lead` / `send_message` paths with an optional attachment/preview protocol for long peer DMs and non-schema prose replies. This directly protects peer/lead context windows without losing auditability.

4. **Renderer/UI as a projection of App Server notifications.** Their compact renderer summarizes command/file/plan/diff/error events and dumps failure tail (`codex-bridge.mjs:427-817`), while Ink handles streams with 50ms flush (`codex-ink-viewer.mjs:262-287`, `306-475`). Target: build a small `claude-anyteam watch-events` or TUI projection over our existing `VisibilityEvent` log rather than relying on tmux stderr. This would close our UI parity gap without giving up structured events.

5. **Crash recovery split by transport health.** Native TUI abnormal exit triggers app-server restart only if WebSocket is down; otherwise just restart the TUI (`codex-bridge.mjs:2518-2527`). WebSocket errors force close after 2s and restart once on a fresh port (`codex-bridge.mjs:1901-1920`, `2081-2131`). Target: our App Server client could learn a similarly explicit transport-health state before classifying visibility degradation vs task failure.

## Anti-patterns we should avoid

1. **CLI-as-protocol instead of MCP tool exposure.** `codex-bridge send` is simple and shell-native, but it relies on prompt obedience and breaks for sub-agents (`teamagent.md:14-17`, `30-39`). It cannot give Codex typed tool schemas, tool-call events, or direct errors like `recipient not a member`. Our wrapper MCP surface is the right direction.

2. **No rich capability declaration.** Prefix routing and static `Available teammates` prompt text (`codex-bridge.mjs:55-64`) are insufficient for §1/§3. They cannot express `thread_fork`, `accepts_peer_steer`, delivery mode, failure modes, or authorization. Do not replace our manifest layer with name conventions.

3. **Unconditional busy-turn steering.** Any active-turn inbound message becomes a `turn/steer` (`codex-bridge.mjs:2801-2819`), and the injected text calls it a leader message (`codex-bridge.mjs:2391-2395`). This is exactly the peer-DM-vs-steer confusion claude-anyteam has been fixing with `messageKind` and manifest-gated `kind="steer"`.

4. **Blind task auto-completion.** `markMyTasksCompleted` marks every own `in_progress` task completed after a successful turn (`codex-bridge.mjs:1684-1704`). That prevents some reschedule races but is unsafe when a worker owns multiple tasks or when a successful prose response is not the task completion. Keep explicit task IDs/status updates.

5. **Direct-target inbox write without membership validation.** `resolveSendTargets` validates/expands only broadcast; a direct target is returned as-is (`codex-bridge.mjs:1439-1447`), and `handleSendCommand` writes to that inbox path (`codex-bridge.mjs:1591-1597`). This can create orphan inboxes for mistyped names. Our `send_message` membership guard (`wrapper_server.py:832-841`) is the safer protocol invariant.

## Honest assessment

They solved several things we have not fully solved:

- **Low-latency inbox wakeup.** Their `fs.watch` + 100ms active loop is an immediate §3 mechanical win over our fixed 1.5s loop. This is the highest-confidence lift.
- **Interactive Codex visibility UX.** Their pane/native TUI rendering makes Codex feel alive in the terminal. We have better structured event data, but not an equally polished live UI projection.
- **Native TUI/WebSocket crash recovery.** They have thought through transport-specific recovery, fresh-port restart, SIGTERM→SIGKILL escalation, and TUI-only restart when the app-server is still healthy. We mostly classify and report; they attempt recovery.
- **Mailbox context bloat control.** The `results/` + preview pattern is simple and effective. We have schema-constrained task outputs, but not a universal long-message attachment convention.

They did **not** solve capability declaration, peer semantic routing, or typed tool exposure better than us. Their design is closer to "Codex teammate command shim" than "capability-preserving heterogeneous protocol layer." It is nevertheless the closest sibling because it uses the actual Codex App Server and the same Claude team mailbox substrate.

The right synthesis is: keep our wrapper-MCP + manifest architecture, but steal their substrate mechanics (watcher, router, preview storage, UI projection, transport recovery). Do not steal their CLI-only messaging or name-prefix-as-capability model.

## Open questions

- Does `codex-bridge` ever register `capabilities` or `agentType` metadata compatible with modern Claude Team config, or is the current repo pre-capability-era and relying solely on name/model routing?
- What is the empirical peer-DM end-to-end RTT under `fs.watch` when the recipient is idle vs active? Source constants imply low wake latency, but model/tool invocation still dominates.
- Is the lack of direct-target membership validation intentional to allow hot-join/missing members, or an oversight?
- Does the single long-lived thread produce cross-task context contamination in large teams? Our `thread/fork` design isolates lineage per task while preserving history; theirs may accumulate all worker history indefinitely.
- Could their native TUI/WebSocket mode coexist with our wrapper MCP subprocesses, or does the native TUI path bypass/obscure wrapper tool calls?
