# nwyin-claude-cleanroom-2-1-83 — research digest

## What is it
`nwyin/claude-cleanroom-2.1.83` is a small reverse-engineering/cleanroom blueprint by Tommy Bui Nguyen (`git show --format=fuller` shows author Tommy Bui Nguyen, commit `928df43`, remote `https://github.com/nwyin/claude-cleanroom-2.1.83.git`). The checkout contains only five Markdown artifacts, not a runnable implementation or package manifest: `ARCHITECTURE.md` plus four files under `extracted/`.

It documents Claude Code v2.1.83 as decompiled from a Bun-compiled Mach-O binary and extracted JS bundle, then turns that into a minimal-harness implementation plan. The stated core is a streaming Anthropic Messages API loop: build prompt, stream model output, parse `tool_use`, execute tools, feed `tool_result`, repeat until `end_turn` or abort (`ARCHITECTURE.md:1-27`, `extracted/orchestration.md:7-18`).

## Scope: in vs out
**Observed repo contents:** documentation only. There are no package manifests, source files, tests, or generated bundles in this checkout (`git ls-tree` lists only the Markdown files). Treat it as prior-art notes / cleanroom requirements, not an implementation to run.

**Blueprint says a minimal harness must implement:**
- Infinite multi-turn tool loop, tool result feedback, streaming event parsing, JSON-schema tool definitions, modular prompt assembly, and retry/fallback handling (`ARCHITECTURE.md:431-445`).
- At least simple context compaction, permission gating, parallel execution for concurrency-safe tools, and large-output persistence (`ARCHITECTURE.md:447-455`).
- Core tool schema mechanics: tool object includes name, aliases, search hint, strict/defer flags, max result size, Zod input schema, dynamic prompt/description, execution, validation, result mapper, concurrency and read-only classifiers (`ARCHITECTURE.md:87-111`).

**Blueprint explicitly says can be omitted initially:** deferred tool loading / ToolSearch, subagents, hooks, MCP integration, auto-memory, the security-classifier LLM call, fast/thinking modes, and cache annotations (`ARCHITECTURE.md:457-466`). This is the cleanroom scope boundary: build the loop and safe local tools first; defer the larger Claude Code ecosystem.

**Why if stated:** mostly complexity and cost. Subagents and hooks are described as complex/nice-to-have; MCP and auto-memory are “separate concern”; the security classifier costs an extra LLM call; cache control and fast/thinking are optimizations or parameters (`ARCHITECTURE.md:457-466`).

## Architecture summary
Transport is Anthropic Messages API streaming plus an internal async-generator event bus. The turn lifecycle is: microcompact, optional autocompact, build prompt/tool schemas, stream API, collect tool uses, execute tools, inject attachments, check turn limits, and loop (`ARCHITECTURE.md:158-198`; `extracted/orchestration.md:59-92`).

The tool surface is large and schema-first: 35 named tools, a core set (Bash/Read/Edit/Write/Grep/Glob/Agent/Skill), many deferred tools including Task*, Team*, SendMessage, PlanMode, Worktree, Cron, MCP resources, and internal-only security/explain tools (`ARCHITECTURE.md:79-85`; `extracted/tool_definitions.md:56-99`). Tool schemas are built dynamically from Zod and can vary by context, permissions, active agents, and config (`extracted/tool_definitions.md:1792-1799`).

Lifecycle is handled inside one Claude Code harness. Termination and state are explicit (`completed`, `aborted_*`, `max_turns`, `hook_stopped`, `model_error`, etc.); hooks fire at PreToolUse/PostToolUse/Stop/SubagentStart/SubagentStop/TaskCompleted/TeammateIdle and can alter inputs, block, or wake the model (`extracted/orchestration.md:95-118`, `extracted/orchestration.md:737-751`; `extracted/additional_architecture.md:286-384`).

Team surface exists, but as native Claude Swarm tools/prompts rather than a heterogeneous wrapper protocol. `SendMessage` takes `to`, optional `summary`, and a `message` union (plain string or typed shutdown/plan-approval responses) (`extracted/tool_definitions.md:1595-1630`). Team context enters the prompt through attachments such as `teammate_mailbox`, `team_context`, `agent_pending_messages`, `unified_tasks`, plan-mode status, and deferred-tool deltas (`extracted/additional_architecture.md:512-536`).

## §1 (harness preservation) comparison
Single-harness / single-agent-loop, not heterogeneous harness preservation. It preserves Claude Code’s own harness deeply (React/Ink TUI, Anthropic streaming, Claude Code tools, hooks, subagents, MCP, task/team tools), but it does not preserve Codex/Gemini/Kimi native loops. Even model variation is within Anthropic-facing Claude Code semantics (Opus/Sonnet/Haiku, Bedrock/Vertex provider plumbing), not external CLI harnesses.

Against claude-anyteam §1, this is the “host loop owns everything” architecture. It is useful prior art for how one harness represents tools, lifecycle, and team messages, but if copied as the universal runtime it would flatten Codex App Server `turn/steer`/`thread/fork`, Gemini ACP permission bridging, and Kimi native swarm/large-context behavior. We should lift the schema and lifecycle patterns, not the single-loop ownership model.

## §2 (visibility parity) comparison
Native Claude has strong lead visibility because the loop yields message chunks, raw stream events, progress events, tool results, and internal attachments into the UI/event stream. The docs describe `stream_event`, `stream_request_start`, `progress`, `attachment`, `grouped_tool_use`, and tombstone message types (`extracted/orchestration.md:683-699`), plus async tool-use summaries and telemetry checkpoints (`extracted/orchestration.md:88-90`, `extracted/orchestration.md:753-806`).

For subagents, completion result content includes `agentId`, `worktreeBranch`, and `tool_uses` counts in the parent tool result (`extracted/orchestration.md:665-679`). This is close to our §2 target for lead visibility: do not collapse teammate activity into final prose; carry machine-visible progress and identifiers.

Gap versus claude-anyteam: this visibility is native to Claude’s own loop. It does not show how to surface a different harness’s host-tool activity without wrapping it. Our adapter still needs backend-specific event extraction and wrapper-tool instrumentation to make Codex/Gemini/Kimi activity visible to the Claude lead.

## §3 (peer efficiency) comparison
Peer-DM exists as a first-class `SendMessage` tool. Its schema allows direct teammate names, broadcast `*`, a UI preview summary, plain message text, and typed protocol replies for shutdown and plan approval (`extracted/tool_definitions.md:1595-1630`). The teammate communication prompt is unusually explicit: team agents must use SendMessage for teammate communication, and ordinary prose is not visible to teammates (`extracted/system_prompts.md:996-1014`). I DM’d team-lead immediately with this §3 prompt-pattern win.

Capability discovery is not peer-specific. It is tool discovery: tools have `searchHint`, `shouldDefer`, and dynamic descriptions; `ToolSearch` can directly load by `select:<tool_name>` and returns matched schemas (`extracted/tool_definitions.md:103-167`, `extracted/tool_definitions.md:902-944`). There is no equivalent of our `capability_manifest` with per-teammate unique primitives, invocation guidance, and failure modes.

Efficiency risk: `SendMessage` itself is listed as deferred (`extracted/tool_definitions.md:163-165`, `extracted/tool_definitions.md:1595-1600`). Native Claude mitigates that with the strong team prompt and ToolSearch. In our observed routed-backend failure mode (“I don’t have send_message”), deferring coordination tools would be an anti-pattern; our wrapper protocol tools should remain directly exposed and repeatedly named.

## Protocol-layer lessons (transport)
**Wire shape there:** internal Messages API conversation, SSE/streaming events, tool_use/tool_result blocks, and prompt attachments. MCP adds independent transports: stdio, SSE, HTTP, WebSocket, IDE variants, SDK, and Claude.ai proxy; MCP tools are namespaced as `mcp__<server>__<tool>` (`ARCHITECTURE.md:421-427`; `extracted/additional_architecture.md:189-251`).

**Wire shape ours:** file-based Agent Teams contract: `~/.claude/teams/{team}/config.json`, `inboxes/{name}.json`, task files, file locks, and wrapper MCP tools for routed backends. We should not replace our substrate with their API loop, but several wire-shape details are worth lifting:

- Preserve structured message kinds at the transport boundary. Their `SendMessage.message` union carries lifecycle payloads (shutdown, shutdown response, plan approval response) rather than asking models to encode lifecycle in prose (`extracted/tool_definitions.md:1615-1624`). This validates our `messageKind`/typed-envelope direction.
- Treat attachments as versioned prompt-input deltas, not prose blobs. Their turn setup always attaches changed files, deferred-tool deltas, mailbox/team context, plan-mode state, and pending messages (`extracted/additional_architecture.md:512-536`). Our peers need analogous typed inbox/capability deltas cached at team formation and refreshed by broadcast, not ad hoc “remember to...” text.
- Use concurrency/read-only classifiers as transport metadata for tool execution. Native tools expose `isConcurrencySafe()` and `isReadOnly()` and the executor uses those to parallelize or serialize (`ARCHITECTURE.md:104-110`; `extracted/orchestration.md:261-272`). Our wrapper MCP could annotate safe read-only team tools separately from mutating task/inbox tools.

## Protocol-layer lessons (capability)
Claude Code has excellent tool capability description but weak peer capability description. Each tool has schema, aliases, search hint, dynamic prompt, validation, result mapper, max-result policy, read-only, and concurrency flags (`ARCHITECTURE.md:87-111`; `extracted/tool_definitions.md:1792-1818`). That is close to MCP tool manifest quality.

But peer uniqueness is not modeled. The team tool surface is feature-gated (`Task*`, `Team*`, `SendMessage`) and not per-teammate (`extracted/tool_definitions.md:1810-1816`). There is no declaration like “this teammate accepts peer steer”, “this teammate has thread_fork”, or “this teammate has large_context”. For claude-anyteam, this reinforces the split in `CLAUDE.md`: keep transport universal, then add a richer per-harness capability layer.

Liftable capability patterns:
- `searchHint` for every manifest capability: peers need short semantic hints for when to discover/invoke a capability, not only a capability name.
- Direct selection path: `ToolSearch(select:<tool_name>)` is a cheap deterministic load path (`extracted/tool_definitions.md:915-918`). Our `capability_manifest` should support deterministic lookup and cache priming for a specific teammate/capability pair.
- Dynamic descriptions: tool descriptions vary by context and active agents (`extracted/tool_definitions.md:1798-1799`). Our rich manifests can similarly include delivery mode, authorization, and current availability rather than static docs only.

## Protocol-layer lessons (lifecycle)
Spawn/lifecycle is more complete than most clones:
- Agent tool schema can name spawned agents, make them addressable via SendMessage, set team name, permission mode, and worktree isolation (`extracted/tool_definitions.md:640-760`).
- Full subagent orchestration resolves model, creates an agent ID, sets up MCP, builds prompt, preloads skills, runs SubagentStart/Stop hooks, records transcript, and cleans up MCP (`extracted/orchestration.md:612-639`).
- Context isolation is explicit: fresh read-file state, abort controller, tool decisions, replacement state, UUID, and query depth (`extracted/orchestration.md:642-655`).
- Plan approval is modeled as tools (`EnterPlanMode`, `ExitPlanMode`) and protocol reply payloads (`plan_approval_response` in SendMessage) (`extracted/tool_definitions.md:1197-1247`, `extracted/tool_definitions.md:1621-1623`).
- Permission is layered: pre-tool hooks can allow/deny/ask/modify, user permission callback can modify input, and modes include default, auto, plan, bypass, acceptEdits, dontAsk, and bubble (`ARCHITECTURE.md:292-317`; `extracted/orchestration.md:530-562`).
- Idle/task-complete are hooks with lifecycle semantics, not just textual notifications (`extracted/orchestration.md:737-751`).

Comparison to ours: our lifecycle is distributed across file mailbox/task state plus per-backend adapters. We should retain that distribution to preserve harnesses, but adopt the native discipline that lifecycle events are typed, hookable, and can block/wake continuation. `TeammateIdle` especially maps to our idle-notification noise problem: it should be a typed event kind with consumer filters, not a prose message.

## Lessons we should adopt
1. **Harden peer-DM prompting with a visibility invariant.** Add the native team prompt’s semantic warning to all routed task/prose prompts: plain final text does not reach peers; protocol messages must use `send_message`. Target: `src/claude_anyteam/prompts.py`, `src/claude_anyteam/backends/gemini/prompts.py`, `src/claude_anyteam/backends/kimi/prompts.py`.

2. **Use a typed union for lifecycle payloads on the peer-DM surface.** Native `SendMessage` accepts ordinary text and structured shutdown/plan-approval payloads in one schema (`extracted/tool_definitions.md:1615-1624`). Our `send_message(body, kind)` is good for peer-DM kind, but lifecycle responses should remain typed envelopes end-to-end so recipients never parse prose.

3. **Cache and refresh prompt attachments as deltas.** Native turn setup injects `deferred_tools_delta`, `mcp_instructions_delta`, `teammate_mailbox`, `team_context`, and `agent_pending_messages` (`extracted/additional_architecture.md:512-526`). Our capability roster/manifest should be primed at team formation and refreshed by typed broadcast, matching `CLAUDE.md §3` on capability-discovery latency.

4. **Annotate wrapper tools/capabilities with concurrency and read-only metadata.** Native execution uses `isConcurrencySafe()` and `isReadOnly()` (`extracted/tool_definitions.md:1806-1808`). For our wrapper MCP, `read_inbox`, `task_list`, `read_config`, and `capability_manifest` should be explicitly read-only/concurrency-safe; `send_message`/`task_update` mutate and should serialize under file locks.

5. **Model idle/task-complete as lifecycle hooks/events, not prose.** Native `TeammateIdle` and `TaskCompleted` are named hook events (`extracted/orchestration.md:747-749`). Our substrate should keep heartbeat idle, substantive peer-DM, task_complete, permission, and plan approval as typed event kinds with cheap filtering.

## Anti-patterns to avoid
1. **Do not clone the single-loop architecture as our coordinator.** It is ideal for native Claude Code but would flatten external harnesses into Claude tool calls, violating §1.

2. **Do not defer critical coordination tools for routed teammates.** Native can defer `SendMessage` because Claude has a strong prompt and ToolSearch path; our observed Codex/Kimi/Gemini peers already sometimes fail tool discovery. Keep `send_message`, `task_update`, `read_inbox`, and `capability_manifest` directly exposed and named.

3. **Do not encode lifecycle by prose convention.** Native SendMessage’s typed plan/shutdown payloads show the right direction. Free-text “please approve plan” or “I am idle” messages are brittle and cause §3 filtering costs.

## Open questions
- The cleanroom docs mention `teammate_mailbox` and team context attachments, but do not document the on-disk Agent Teams mailbox layout. Is native Claude v2.1.83 still file-backed under the hood, or is this an extraction gap?
- If `SendMessage` is deferred in native Claude, what guarantees it gets loaded in team mode before first peer DM? Is the prompt enough, or is there a hidden non-deferred override when team mode is active?
- How does native `TaskOutput` expose live/background agent output to peers and lead? Could its polling/summary shape inform our `task_complete` + live event stream?
- Are `TeammateIdle` and `TaskCompleted` hooks user-extensible in team mode, or internal-only in practice?
