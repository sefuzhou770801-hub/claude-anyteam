# maorinka-claude-rs — research digest

## What is it

`maorinka-claude-rs` is a from-scratch Rust reimplementation of Claude Code, organized as a Cargo workspace (`claude-core`, `claude-tools`, `claude-tui`, `claude-cli`). It is not a wrapper around the official CLI and not a heterogeneous harness router: its center of gravity is Claude/Anthropic API parity with the TypeScript client, plus a partial Rust-native team/agent layer.

The repo is substantial but explicitly rough. Its own parity notes say many gaps remain in query behavior, tool executor depth, MCP resources, bridge/direct-connect, UI, migrations, analytics, hooks, permission plumbing, and exact tool exposure/gating.

## Architecture summary

**Process model.**

- `claude-cli` is the binary entry point. It builds auth/config, registry, MCP manager, `QueryEngine`, cancellation token, and either launches a ratatui TUI or runs a noninteractive agentic loop.
- `claude-core` owns API/SSE streaming, query state, MCP clients/managers, config/auth, bridge/proxy skeletons, hooks/types, teams, and session/context helpers.
- `claude-tools` owns local tool implementations and the registry (`Bash`, `Read`, `Write`, `Edit`, `Grep`, `Glob`, `WebFetch`, `WebSearch`, `Agent`, `Task*`, team tools, MCP wrappers, etc.).
- `claude-tui` runs a proper async terminal app: input/render/spinner/engine are separate tasks connected by `tokio::mpsc` events and `EngineCommand`s. The engine owns `QueryEngine` and the event loop never awaits it directly.
- `AgentTool` launches another `claude-rs` subprocess via `tokio::process::Command`, optionally inside a temporary git worktree. Foreground mode waits for the subprocess output; background mode registers an in-process task and streams process output into the task store.
- The team layer detects `tmux` vs in-process backends. `tmux` creates panes/sessions and sends the spawned `claude-rs` command into the pane. The in-process backend registers and seeds a mailbox message but is not a full running teammate supervisor.

**IPC.**

- Internal query/UI streaming uses `tokio::mpsc` channels with `StreamEvent` variants.
- MCP uses JSON-RPC-ish clients over stdio, SSE, HTTP, and WebSocket transports. Stdio/WS maintain pending request maps keyed by request id and fulfilled by `oneshot` senders.
- MCP startup has a strong bounded-concurrency pattern: partition local stdio and remote servers, run local and remote buckets concurrently, cap each bucket separately, and auth-cache-skip remote servers known to need auth.
- Team mailboxes are file-based. `claude-core/src/teams/mailbox.rs` writes arrays under `~/.claude/teams/{team}/inboxes/{agent}.json` guarded by `.lock` files and retry/backoff. `claude-tools/src/send_message.rs` separately writes unique files under `~/.claude/mailboxes/{agent}/msg_<uuid>.json`.
- Tmux backends communicate by pane commands and file mailboxes, not a structured RPC stream.

**Tool surface.**

- Local tool coverage is broad, but its own gap docs call many tools thin relative to TS behavior.
- `StreamEvent` includes `RequestStart`, `ToolStart`, `ToolProgress`, `ToolResult`, `ThinkingDelta`, `TextDelta`, `Compacted`, `UsageUpdate`, `Done`, and `Error`.
- `SendMessageTool` exists and declares itself concurrency-safe because each message lands in a unique file. However, it is not registered in `build_default_registry_with_options()`. The prompt examples use `message`, while the schema requires `content`.
- `ListPeersTool` is gated behind `UDS_INBOX`. Team create/delete are gated behind agent-swarm/internal feature checks. Default registry gating still appears inconsistent: some tools are always registered and then optionally registered again behind flags.

**Lifecycle.**

- Query turns stream Anthropic SSE incrementally, emit deltas/tool starts, support idle stream timeout, and use `CancellationToken` for cancellation. On cancellation after tool blocks were seen, the engine synthesizes tool results to keep conversation history structurally valid.
- MCP child processes are disconnected with bounded cleanup (SIGINT -> SIGTERM -> SIGKILL, then abort reader task).
- The TUI swaps cancellation tokens after Escape-cancel so later turns can be independently cancelled.
- Team shutdown/permission/plan messages exist as mailbox helpers, but the message struct is still mostly prose fields (`from`, `text`, `timestamp`, `read`, optional `color`/`summary`) rather than a first-class typed `messageKind`.

## §1 (harness preservation) comparison

`maorinka-claude-rs` is largely **single-harness / single-LLM**. It tries to preserve Claude Code semantics by reimplementing the official TypeScript client in Rust; it does not preserve arbitrary external harnesses as native peers.

Against claude-anyteam's §1 north star:

- It preserves one harness family by cloning/recreating it, not by adapting heterogeneous CLIs without flattening them.
- `AgentTool` spawns another `claude-rs` process, so subagents inherit the same Rust Claude harness rather than retaining Codex/Gemini/Kimi/other native affordances.
- MCP can import external tools and transports, but MCP is not harness preservation. It does not model each teammate's CLI as its own capability surface.
- Team backend selection (`tmux` vs in-process) is a process-management choice, not a heterogeneous harness abstraction.

So the useful lesson is negative: rewriting everything into one Rust Claude client gains internal control, but it sacrifices the "preserve actual teammate harness" requirement that motivates claude-anyteam.

## §2 (visibility parity) comparison

The repo has a good **internal event taxonomy**:

- SSE text and thinking deltas are streamed.
- Tool starts are surfaced as soon as `ContentBlock::ToolUse` completes.
- `StreamEvent` has slots for tool progress/results, usage updates, compaction, errors, and terminal status.
- Noninteractive `stream-json` emits stream events and explicit tool result records.
- The TUI has `MessageEntry::ToolResult`, permission responses, AskUser dialog flow, and background engine/task events.

But parity is incomplete:

- The core query engine emits `ToolStart`; the actual tool result emission is caller-managed in CLI/TUI. There is no single lead-facing transcript equivalent to "all tool/prose/errors from routed teammates, exactly as native."
- `ToolProgress` exists but is mostly not wired. Foreground Bash waits for process completion and then reads stdout/stderr, so long verbose commands do not naturally stream progress. The Bash background path is closer: it reads stdout/stderr concurrently and appends bytes to an output file.
- `StreamingToolExecutor` exists but is simplified and not fully integrated in the noninteractive loop; the CLI still executes tool uses sequentially in a `for` loop.
- Team mailboxes expose prose-ish messages, not typed event envelopes. The team lead cannot reliably distinguish idle/status/permission/substantive messages without parsing text or embedded JSON.
- `SendMessageTool` being unregistered creates the worst §2/§3 visibility failure: the prompt can instruct "call SendMessage", but the actual model tool surface may not include it.

Net: good local streaming primitives; no complete cross-peer visibility parity layer.

## §3 (peer efficiency) comparison

**Rust-specific lens: primitives Rust unlocks.**

The repo shows several Rust patterns that are directly relevant to our M11a p95 (~557s for kimi+codex; target ~30-60s):

1. **Long-lived async supervisors.** The TUI engine task owns `QueryEngine`; app code sends cheap channel commands and receives events. That shape maps to an anyteam adapter where each peer has a resident supervisor instead of "wake, poll, infer, invoke" per turn.
2. **Bounded concurrent fan-out.** `McpManager::connect_all_respecting_auth_cache()` splits local and remote work, caps each class, runs buckets concurrently, and refreshes cached tool definitions once. This is the pattern we should use for peer capability discovery and manifest prewarm.
3. **Pending request maps with `oneshot`.** MCP stdio/WS transports correlate responses by id and unblock the exact waiter. Peer DMs and capability probes should have the same cheap, typed rendezvous rather than global polling.
4. **Structured cancellation/heartbeat.** `CancellationToken`, stream idle timeouts, MCP heartbeat guards, and bounded child cleanup prevent hung tools/processes from eating whole peer latency budgets.
5. **Concurrent pipe handling.** The background Bash path uses `tokio::select!` to read stdout/stderr without blocking. The foreground Bash path fails to use this fully; that contrast is instructive.

**What closes the M11a p95 gap.**

- Prewarm peer capability manifests at team formation with bounded fan-out, not at first attempted peer contact.
- Replace mailbox polling with evented file notification. Rust could use `notify`/inotify/kqueue/FSEvents; Python can use `watchfiles`/`watchdog` or a tiny Rust sidecar. The important semantic is "append -> notify -> enqueue", not "append -> wait until a future model/tool round notices".
- Run one resident supervisor per teammate with a bounded inbound queue. The supervisor should own inbox watches, process handles, cancellation, and output streaming, and should expose all transitions as typed events.
- Make peer DM a cheap write+notify+ack path. A model should not need to rediscover the tool, infer schema, or wait for a loop tick before the message is considered delivered.
- Add heartbeat and timeout telemetry around every routed peer call, so slow RTT decomposes into "tool not visible", "message write", "watcher wake", "peer busy", "response generation", etc.

**What maorinka does not solve.**

- `SendMessageTool` is not registered in the default tool registry.
- There are two incompatible mailbox substrates (`~/.claude/mailboxes/...` and `~/.claude/teams/.../inboxes/...`).
- Mailbox polling is explicit (`start_mailbox_polling(agent_id, interval)`), not evented.
- In-process teams are a registration/mailbox skeleton, not true concurrent teammates running query loops.
- Task state is in-memory, process-local, and therefore not a team-wide substrate.

So the §3 win is not "copy maorinka's team messaging"; it is "copy the Rust supervisor/concurrency shape and avoid maorinka's mailbox/tool-surface split."

## Lessons we should adopt

1. **Supervisor-first adapter architecture.** Model `wrapper_server.py` / peer adapters after the TUI engine pattern: resident task owns the harness, channels carry commands/events, and the coordinator never blocks on direct harness internals.

2. **Bounded concurrent capability prewarm.** Borrow the MCP manager shape for peer discovery: partition by backend class, fan out with separate caps, cache auth/failure/manifest results, and do one refresh barrier before the lead starts delegating.

3. **Evented inbox delivery.** Convert peer mailbox delivery from polling/next-turn awareness into file-watch or socket events. If pure Python cannot hit target reliably, a tiny Rust sidecar using `notify` plus a simple local socket would be a focused win without rewriting the harness layer.

4. **Typed request/response rendezvous.** Use request ids, `oneshot`/future completion, and typed event envelopes for peer DMs, permission requests, idle notices, and tool results. Do not ask the lead to infer these from prose.

5. **Bounded process cleanup and stall detection.** Standardize SIGINT -> SIGTERM -> SIGKILL deadlines, stream idle timeouts, and heartbeat guards for every backend child. Emit those as visibility events.

## Anti-patterns to avoid

1. **Prompt/tool/schema drift.** `SendMessageTool` exists but is not registered, while its prompt uses `message` and its schema requires `content`. This reproduces exactly the "instruction says send_message but model cannot actually call the right thing" failure mode.

2. **Multiple mailbox substrates.** Separate `~/.claude/mailboxes/{agent}/msg_<uuid>.json` and `~/.claude/teams/{team}/inboxes/{agent}.json` paths make it unclear which transport is authoritative. One team message substrate should own delivery, read state, typing, and observability.

3. **Having concurrency primitives but keeping sequential execution.** Rust exposes `JoinSet`, `mpsc`, `CancellationToken`, `select!`, and safe concurrent file writes, yet the main noninteractive loop still executes tool calls sequentially and foreground Bash waits before reading pipes. We should ensure any concurrency primitive lands in the critical path, not just in helper modules.

## Open questions

- Was `SendMessageTool` intentionally left unregistered, or is it a parity bug?
- Which mailbox path was intended to be canonical: `claude-tools` mailboxes or `claude-core` team inboxes?
- Does any runtime path actually start `start_mailbox_polling()`, and at what interval?
- Should claude-anyteam implement evented inboxes in Python (`watchfiles`/`watchdog`) first, or ship a minimal Rust sidecar for notify/kqueue/inotify plus process supervision?
- Could the `StreamingToolExecutor` pattern be wired into all tool execution paths in maorinka, and can we mirror that for peer-capability-safe parallelism?
