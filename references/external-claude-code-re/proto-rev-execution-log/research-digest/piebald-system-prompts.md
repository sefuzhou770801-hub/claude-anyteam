# piebald-claude-code-system-prompts — research digest

## Corpus shape

- Local corpus path mined: `references/external-claude-code-re/piebald-claude-code-system-prompts/repo/`.
- File counts:
  - `find ... -type f`: **323** files including the embedded shallow `.git/` metadata.
  - Non-`.git` repo files: **294**.
  - Prompt markdown files under `system-prompts/`: **288**.
- Prompt-file categories, by filename prefix:
  - **37** `agent-prompt-*` files
  - **38** `data-*` files
  - **30** `skill-*` files
  - **64** `system-prompt-*` files
  - **40** `system-reminder-*` files
  - **78** `tool-description-*` files
  - **1** `tool-parameter-*` file
- Version coverage:
  - README says corpus is current as of **Claude Code v2.1.120 (2026-04-24)** and has a changelog across **162 versions since v2.0.14**.
  - Local `CHANGELOG.md` has **121 changed-prompt version entries**, from **v2.0.14** through **v2.1.120**.
  - Each prompt file has a `ccVersion`; the 288 prompt files span **55 distinct `ccVersion` tags**, earliest `2.0.14`, latest `2.1.120`.
  - NPM metadata check: `@anthropic-ai/claude-code@2.0.14` was published **2025-10-10**; `2.1.120` was published **2026-04-24**. So local corpus version span is **2025–2026**. NPM already showed `2.1.121` on **2026-04-27**, not represented in this local Piebald snapshot.
- Corpus is not just one monolithic system prompt. It includes main system prompt fragments, system reminders, tool descriptions, agent/subagent prompts, skills, and embedded reference/data docs. This matters: many behavior-critical instructions live in tool descriptions and reminders, not only “the system prompt.”

## Tool-discovery patterns

Claude Code patterns that pre-empt “I can’t find this tool” are mostly three-part: explicit availability, exact name/schema loading path, and an imperative that the model must call the tool rather than answer in prose.

1. **Deferred tool schemas have an explicit loading mechanism.** Source: `system-prompts/tool-description-toolsearch-second-part.md`.

   > “Until fetched, only the name is known — there is no parameter schema, so the tool cannot be invoked.”
   > “Once a tool's schema appears in that result, it is callable exactly like any tool defined at the top of the prompt.”

   The useful pattern is not merely “tool exists”; it explains the transitional state: name-known/schema-not-loaded. It also gives exact query forms such as `select:Read,Edit,Grep`.

2. **MCP tools that require deferred loading get a concrete two-step recipe.** Source: `system-prompts/system-prompt-chrome-browser-mcp-tools.md`.

   > “Before using any chrome browser tools, you MUST first load them using ToolSearch.”
   > “First: ToolSearch with query `select:mcp__claude-in-chrome__tabs_context_mcp`; Then: Call ...”

   This is a direct anti-flap pattern: if a tool is hidden behind discovery, do not conclude absence; load it, then call it.

3. **Availability claims must be grounded before the model asserts absence.** Source: `system-prompts/skill-computer-use-mcp.md`.

   > “Look before you assert.”
   > “If you're about to say an app doesn't support an action, that claim should be grounded in what you just saw...”

   This is portable to tool discovery: before saying `send_message` is unavailable, inspect the actual tool surface or attempt the exact tool call.

Additional relevant pattern:

- Source: `system-prompts/skill-computer-use-mcp.md`: “You have a computer-use MCP available (tools named `mcp__computer-use__*`).” This combines presence + naming convention + capability in one sentence.
- Source: `system-prompts/tool-description-skill.md`: “Available skills are listed in system-reminder messages...” and “Never guess or invent a skill name from training data.” This draws a boundary between enumerated capabilities and hallucinated ones.

## Multi-agent / peer-DM patterns

Claude Code has strong, repeated peer-DM language. The strongest source is `system-prompts/tool-description-sendmessagetool.md`:

> “Your plain text output is NOT visible to other agents — to communicate, you MUST call this tool.”
> “Refer to teammates by name, never by UUID.”

`system-prompts/system-reminder-team-coordination.md` adds a compact runtime reminder:

- “You are a teammate in team ...”
- It names identity, team config path, task list path.
- “The team lead's name is `team-lead`.”
- It gives a canonical message object with `to`, `message`, and `summary`.

`system-prompts/tool-description-teammatetool.md` reinforces operational rules:

- Messages are automatically delivered; do not poll an inbox manually.
- Idle means waiting for input, not unavailable.
- Idle teammates can receive messages; sending a message wakes them.
- Team config contains `members[].name`; names are used for `to` and task owners.
- “Your team cannot hear you if you do not use the SendMessage tool.”
- Do not send structured JSON status; use TaskUpdate for status and plain text for peer messages.

Canonical phrasing to lift for our world:

- “Your plain text output is not delivered to the teammate. To reply to a teammate, you MUST call `send_message`.”
- “Use teammate names exactly as in `read_config().members[].name`; never UUID/session IDs.”
- “Messages are delivered automatically; use `read_inbox` only when explicitly checking pending replies.”

## Capability-declaration patterns

Capability boundaries are explicit and attached to the tool/agent surface, not left to the model to infer.

- `tool-description-teammatetool.md`: “Each agent type has a different set of available tools — match the agent to the work.” It then distinguishes read-only agents, full-capability agents, and custom agents with their own restrictions.
- `tool-description-agent-usage-notes.md`: context-specific parameter availability is spelled out, e.g. `name`, `team_name`, and `mode` are unavailable in teammate context; omit them.
- `data-managed-agents-core-concepts.md`: agent configuration declares `tools`, `mcp_servers`, and `skills` as top-level fields; MCP servers are standardized third-party capabilities with unique names.
- `data-managed-agents-tools-and-skills.md`: MCP capability is declared separately from credentials: agent creation declares server name/URL and toolset; sessions attach vault credentials. This separates “capability exists” from “secret/auth is available.”
- `system-prompt-harness-instructions.md`: denied tool calls mean permission was declined; adjust instead of retrying verbatim.
- `skill-agent-design-patterns.md`: promote actions to dedicated tools when you need to gate, render, audit, or parallelize them. Sending messages is explicitly named as a hard-to-reverse/external action that benefits from a dedicated tool.

Pattern: **identity + exact invocation schema + when-to-use + when-not-to + boundary/failure behavior**. This mirrors our own capability-manifest north star.

## Lessons for our send_message tool-discovery flap

**Highest-priority finding:** our current Codex teammate prompt does list “MCP tools available,” but Claude Code’s own peer-DM prompt is more forceful and tool-centric. It says the peer cannot see prose, so the model must call the tool. We should patch both the teammate brief and wrapper MCP tool description with this stronger pattern.

### Proposed prompt patch: prose replies / peer DMs

Source lift:

- From `tool-description-sendmessagetool.md`: “plain text output is NOT visible to other agents — to communicate, you MUST call this tool.”
- From `system-prompt-chrome-browser-mcp-tools.md`: explicit load-then-call pattern.
- From `skill-computer-use-mcp.md`: “Look before you assert.”

Patch text for `v7_prose_reply_prompt`:

```text
# Team messaging tool
`send_message` is available in this session via the claude-anyteam wrapper MCP.
It is exposed under the exact lowercase name `send_message`.
Your plain text output is not delivered to other teammates; to reply to a teammate, you MUST call:

send_message(to='<sender-name>', body='<your reply>', summary='<5-10 word preview>')

Do not say you lack `send_message` unless an actual attempted tool call fails.
If the visible tool list seems incomplete, treat this as tool-discovery latency: use the available MCP/tool discovery surface or inspect the wrapper MCP tool list, then call `send_message`.
Do not invent alternate names. Do not use terminal output as a substitute for the team message.
```

Rationale: It turns “Use the tool” into “this is the delivery path; prose is invisible.” It also blocks the exact hallucinated sentence before it is generated.

### Proposed prompt patch: task execution prompts

Patch `v7_task_prompt` current “MCP tools available” section into a more Claude-Code-shaped “Protocol tools” block:

```text
# Protocol tools available through wrapper MCP
These tools are callable now. Tool names are exact and lowercase.
- `send_message(to, body, summary?, kind?)`: send a DM/status/handoff to `team-lead` or any peer. Plain prose is not delivered to peers; if you are responding to a teammate, call this tool.
- `task_update(task_id, active_form?, status?, owner?, metadata?)`: claim/update task state. Use this for status; do not send structured JSON status messages.
- `task_create(subject, description)`: split off discovered work.
- `read_config()`: discover teammate names and capabilities.
- `read_inbox(unread_only?)`: inspect pending DMs if needed.
- `task_list()`: inspect task state.

Before claiming a protocol tool is unavailable, call or discover it by exact name. A missing schema/listing is a discovery issue, not evidence that the wrapper lacks the tool.
```

### Proposed wrapper MCP tool-description patch

Current `send_message` docstring is good but softer than Claude Code’s. Add near the top:

```python
"""Send a message to another teammate (team-lead or any peer).

Your plain text/final answer is NOT delivered to teammates as a DM. To reply
to a teammate or coordinate with the team, you MUST call this tool. The tool is
exposed under the exact lowercase name `send_message`; do not use `SendMessage`,
`mcp_anyteam_send_message`, UUIDs, or terminal prose as substitutes. If you are
unsure of the roster, call `read_config()` first and use members[].name.
...
"""
```

This pushes the no-prose/no-alias rule into the actual tool schema description, which is where Claude Code puts the equivalent instruction.

### Proposed discovery fallback

Add an always-available wrapper tool or prompt-visible line:

```text
If tool discovery appears stale, `read_config()` confirms team identity/roster and the wrapper MCP advertises: send_message, task_update, task_create, read_inbox, task_list, read_config. Do not infer absence from a missing high-level UI list.
```

Better if implemented as a tiny `list_protocol_tools()` or included in `read_config()` result, because Piebald’s ToolSearch pattern gives the model a concrete check instead of an assertion.

### Meta-observation from this task

This run reproduced the class of bug in miniature: the model-facing tool list in this environment did not include a direct `send_message` function, but the wrapper MCP did expose it. I verified with `build_server().list_tools()` and delivered DMs by calling FastMCP directly from Python. So the problem is **not only exposure**; it is **model-facing discovery and instruction salience**. Also, `task_update(task_id="23")` failed because `~/.claude/tasks/994dcbec-.../23.json` is missing; current task registry had 19/20 only. I continued and reported that to team-lead via `send_message`.

## Other lessons we should adopt

1. **Runtime reminders beat one-time brief text.** Claude Code injects `System Reminder: Team Coordination` with identity, resources, leader name, and canonical message JSON. We should add/refresh an equivalent compact reminder in every Codex teammate invocation, especially prose-DM invocations.

2. **Keep tool names exact and concrete.** Claude Code uses examples and exact fields (`to`, `message`, `summary`) near the tool description. Our prompt should include exact lowercase `send_message(to=..., body=...)`, not just prose “use send_message.”

3. **Separate status channels.** Claude Code says: plain text peer messages via SendMessage; task state via TaskUpdate; no structured JSON status messages. Our prompts should consistently say `send_message` for communication, `task_update` for state. This reduces double-send and canned-fallback noise.

4. **Dedicated tools are there for auditability.** `skill-agent-design-patterns.md` says dedicated tools allow gating/rendering/auditing/parallelism, while shell is opaque. We should discourage shell/file fallbacks for team messages except as a last-ditch diagnostic breadcrumb, because they bypass visibility parity.

## Anti-patterns to avoid

1. **Do not let “I don’t see it” become “it doesn’t exist.”** Piebald’s ToolSearch language explicitly models name-known/schema-not-loaded states. Our prompts should forbid absence claims until an exact tool call/discovery attempt fails.

2. **Do not rely on terminal prose or final output for peer communication.** Claude Code says teammates cannot hear you without SendMessage. Shell output may help debugging but is not a protocol DM.

3. **Do not blur aliases.** Native Claude has `SendMessage`; our wrapper exposes lowercase `send_message`. Mixed mentions like `SendMessage`, `mcp_anyteam_send_message`, and `send_message` in the same brief can increase uncertainty. For Codex/Kimi, say exact lowercase only; for Gemini, use its qualified name only in Gemini-specific prompts.

4. **Do not bury critical routing rules only in long task descriptions.** Put no-prose/no-unavailable/no-alias instructions in the actual tool description and a short repeated system reminder.

## Open questions

- Should the wrapper expose a first-class `list_protocol_tools()` / `tool_discovery()` tool so a Codex teammate can verify the wrapper surface without shell/Python?
- Should `read_config()` include the wrapper protocol tool list and exact names as part of the team capability manifest?
- Why did team-lead assign #23 when the task registry for this team only contains 19 and 20? Is there a stale high-watermark/task-sync issue?
- Is the current Codex App Server path sometimes omitting wrapper MCP schemas from the model context even though the subprocess is started successfully?
- Should prose fallback suppression treat explicit “I don’t have send_message” as a routing/tool-discovery error and auto-diagnose rather than deliver that prose to the lead?
