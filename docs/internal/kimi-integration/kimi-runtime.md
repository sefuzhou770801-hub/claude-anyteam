# Kimi CLI runtime internals

Date: 2026-04-25. Installed CLI tested: `kimi 1.39.0` (binary `/home/rosado/.local/bin/kimi`, agent spec versions: `1`, wire protocol: `1.9`, embedded Python: `3.12.3`).

Scope: Kimi side only. This file is the empirical companion to the parity map (`codex-parity-map.md`) and the skills/agent/swarm scope decision (`kimi-skill-and-agent-research.md`). Every probe in this document was run against the installed binary on this host; raw output snippets are quoted verbatim.

## Capability matrix

| Feature | Supported? | Evidence / notes |
| --- | --- | --- |
| Programmatic one-shot invocation | **Yes** | `kimi --print -p <prompt>` runs non-interactively; `--print` implicitly enables `--yolo` per `kimi --help`. Exit `0` on success, `1` on internal limit/error. |
| Interactive invocation | **Yes, but TUI-oriented** | Default `kimi` (no subcommand) launches the interactive Toad TUI; `kimi term` is the same TUI explicitly. Not used as the adapter transport. |
| Machine output formats | **Partial** | `--output-format text \| stream-json`. Bad value exits `2` with usage error. There is no `json` (single-blob) variant — it's text or NDJSON. |
| Streaming response tokens | **No (per-message NDJSON, not per-token)** | `stream-json` emits **one JSON object per chat message**, not per delta. Assistant messages carry their full `content` array (think + text parts) when emitted. There is no progressive token delta wire format on stdout. |
| Tool-call streaming | **Yes (per-message)** | When the model calls tools, additional NDJSON lines appear: an assistant line with `tool_calls` (OpenAI-compatible shape), a `role:"tool"` line per result, then a final assistant line with the textual answer. Built-in tool names are unprefixed PascalCase (e.g. `Shell`, `WriteFile`). |
| MCP support | **Yes** | `--mcp-config-file <FILE>` (repeatable) and `--mcp-config <JSON>` (repeatable). Default config file: `~/.kimi/mcp.json`. `kimi mcp add/remove/list/test/auth/reset-auth` for management. |
| MCP tool naming | **No prefix transformation** | Empirical: server alias `toy` exposed `shout(text)`; the model called it as **`shout`** (bare), not `mcp_toy_shout`. Server alias with an underscore (`any_team`) was accepted unchanged and the tool was still called bare. **Different from Gemini's `mcp_<server>_<tool>` mangling.** |
| Headless approvals | **Yes (`--print` ⇒ `--yolo`)** | `--print` documents "implicitly adds `--yolo`". `--yolo`/`--yes`/`-y` flag exists for interactive, plus `--plan` and `--no-thinking`. |
| Plan mode in print | **Auto-exits** | `--plan` + `--print` writes the plan markdown to `~/.kimi/plans/<random-name>.md`, calls `ExitPlanMode`, sees `Plan approved (auto-approved in non-interactive mode)`, and **proceeds to execute the plan**. Plan mode is not a read-only enforcement in headless. |
| Auth cache reuse | **Yes** | Existing OAuth login is reused. Files live under `~/.kimi/credentials/kimi-code.json` (mode 0600, plus a sibling `kimi-code.lock`). Auth is per-Unix-user, not per-project. |
| Session resume by id | **Yes — but auto-creates on miss** | `kimi -r <id>` (or long form `--session/--resume`) resumes if `<id>` exists under the cwd's session root, otherwise **silently creates a new session with that id**. **No exit code or stderr difference between resume-and-create vs resume-existing.** |
| `-C` / `--continue` | **Yes** | Reads `~/.kimi/kimi.json` `work_dirs[].last_session_id` for the current cwd and resumes that session. Verified with a two-turn token-recall probe: same session id was reused and prior context was retained. |
| Durable transcript | **Yes** | Three files per session under `~/.kimi/sessions/<md5(cwd)>/<session-uuid>/`: `wire.jsonl` (turn/step/contentpart events), `context.jsonl` (system prompt + user/assistant messages + checkpoints), `state.json` (approval / plan-mode / todos / archive flags). |
| Long-lived machine API | **Yes (`kimi acp`)** | JSON-RPC 2.0 server over stdio. Earlier `--acp` flag is deprecated. Initialize handshake returns `protocolVersion: 1`, `agentInfo: {name: "Kimi Code CLI", version: "1.39.0"}`, advertises `loadSession`, `sessionCapabilities.list`/`resume`, MCP HTTP, and an `authMethods[].id = "login"` terminal-auth method. |
| Codex-style app-server sidecar | **No HTTP equivalent** | ACP is the closest analog. No `kimi app-server` and no Codex-style `thread/start` / `turn/steer` methods. Mid-turn steering parity must be designed against ACP `session/cancel` semantics or skipped. |
| CLI schema-constrained output | **No** | No `--output-schema` equivalent. Final structured output must be enforced via prompt + Python jsonschema validation, exactly like the Gemini headless adapter. |
| `--input-format stream-json` | **Yes** | Reads `{"role":"user","content":"..."}` from stdin. Useful for prompts longer than shell argv limits. Pairs with `--print --output-format stream-json`. |

## Process lifecycle and stdio contract

### Recommended one-shot shape

```bash
kimi --print --output-format stream-json [--final-message-only] \
     [--model <slug>] [--no-thinking] [--max-steps-per-turn N] \
     [--mcp-config-file FILE] [--skills-dir DIR] \
     [--session <uuid> | --continue] \
     -p "$PROMPT"
```

Equivalent short flags exist for most: `-p`, `-S`/`-r`, `-C`, `-m`, `-w`, `-h`. The adapter invocation should always use `--print --output-format stream-json` so machine parsing is guaranteed; `--final-message-only` is recommended for terse use, but the multi-message form is the only one that lets the adapter count tool calls (see "Tool-call counting" below).

Observed behavior:

- **stdout** carries the per-message NDJSON in `stream-json` mode. **Non-JSON terminator lines can appear** when limits are hit — e.g. exhausting `--max-steps-per-turn 1` printed `Max number of steps reached: 1` as a plain stdout line after the tool messages. The parser must tolerate non-JSON stdout lines.
- **stderr** carries:
  - The blank line + `To resume this session: kimi -r <uuid>` hint after every successful run.
  - A FastMCP `AuthlibDeprecationWarning` on every invocation that initializes MCP. Benign but noisy.
  - Limit/error diagnostics on the failure path, e.g. `Failed to connect MCP servers: {'toy': RuntimeError(...)}` plus `See logs: /home/rosado/.kimi/logs/kimi.log` and `Run with --debug for full traceback`.
- **stdin** is consumed only when `--input-format stream-json` is set; otherwise the adapter should pass `subprocess.DEVNULL` so the CLI does not block waiting for input.
- **Exit codes** observed empirically:
  - `0` — successful turn (including `kimi -r <unknown-uuid>` which silently creates a new session).
  - `1` — runtime error: MCP connect failure, `Max number of steps reached`, etc. The error text is on stdout, not stderr, in the limit case.
  - `2` — usage error from typer (e.g. `--output-format=bogus`).

### Real stdout sample: trivial prompt

```bash
kimi --print -p "say OK and stop" --output-format stream-json
```

Stdout (single line):

```json
{"role":"assistant","content":[{"type":"think","think":"The user wants me to say \"OK\" and stop. ...","encrypted":null},{"type":"text","text":"OK"}]}
```

Stderr:

```text

To resume this session: kimi -r 3dc086db-76bf-4188-9b53-8892dacbc654
```

### Real stdout sample: tool call

```bash
kimi --print -p "run pwd via shell tool, then say DONE" --output-format stream-json
```

Stdout (three lines, NDJSON):

```jsonl
{"role":"assistant","content":[{"type":"think","think":"...","encrypted":null}],"tool_calls":[{"type":"function","id":"tool_wo4Ct...","function":{"name":"Shell","arguments":"{\"command\": \"pwd\"}"}}]}
{"role":"tool","content":[{"type":"text","text":"<system>Command executed successfully.</system>"},{"type":"text","text":"/home/rosado/Projects/codex-teammate\n"}],"tool_call_id":"tool_wo4Ct..."}
{"role":"assistant","content":[{"type":"think","think":"...","encrypted":null},{"type":"text","text":"DONE"}]}
```

Stderr:

```text

To resume this session: kimi -r 93d86a01-e970-48c2-86a8-c8cb82c0eb28
```

### `--final-message-only` shape

When passed alongside `--output-format stream-json`, every intermediate assistant/tool message is suppressed and the final assistant message **also collapses its `content` from an array of parts to a plain string**:

```json
{"role":"assistant","content":"DONE"}
```

This mode is convenient for `prose_reply` short replies but **destroys the tool-call evidence the adapter needs to count `tool_call_events`** for parity with `CodexResult.tool_call_events`. Adapter must not pass `--final-message-only` for task/plan invocations that need that telemetry.

## Wire-format notes

### Stdout NDJSON taxonomy (`--output-format stream-json`, no `--final-message-only`)

Per-message envelopes only — no init/result events on stdout:

| Line `role` | Required fields | Optional fields | Notes |
| --- | --- | --- | --- |
| `assistant` (intermediate) | `role`, `content[]` (parts: `{type:"think", think}` and/or `{type:"text", text}`) | `tool_calls[]`, `encrypted` per part | Emitted once per assistant step. |
| `tool` | `role`, `content` (string OR array of `{type:"text", text}`), `tool_call_id` | — | One per tool result. Local tool errors land here as text with a `<system>...</system>` prefix. |
| `assistant` (final) | `role`, `content[]` | — | The last assistant line with no `tool_calls`. The adapter's `last_message` is the concatenation of `content[*].type=="text"` text parts on this final line. |

There is no top-level `init`, `result`, or `stats` event on stdout. **Session id is not on stdout.** It is captured from the stderr line `To resume this session: kimi -r <uuid>`.

### Final-message-only shape

```json
{"role":"assistant","content":"<plain string>"}
```

`content` is now a string. No `tool_calls`, no `think` part. This is the only case where `content` is not an array.

### Durable wire log shape (`~/.kimi/sessions/<hash>/<sid>/wire.jsonl`)

Far richer than the stdout NDJSON. Adapter does not need to read it for v1, but it is the source of truth when debugging:

```jsonl
{"type": "metadata", "protocol_version": "1.9"}
{"timestamp": ..., "message": {"type": "TurnBegin", "payload": {"user_input": "..."}}}
{"timestamp": ..., "message": {"type": "StepBegin", "payload": {"n": 1}}}
{"timestamp": ..., "message": {"type": "ContentPart", "payload": {"type": "think", "think": "...", "encrypted": null}}}
{"timestamp": ..., "message": {"type": "ToolCall", "payload": {"type": "function", "id": "tool_...", "function": {"name": "Shell", "arguments": "{...}"}}}}
{"timestamp": ..., "message": {"type": "ToolResult", "payload": {"tool_call_id": "tool_...", "return_value": {"is_error": false, "output": "...", "message": "Command executed successfully.", "display": [...]}}}}
{"timestamp": ..., "message": {"type": "StatusUpdate", "payload": {"context_usage": 0.04, "context_tokens": 11569, "max_context_tokens": 262144, "token_usage": {...}, "message_id": "chatcmpl-...", "plan_mode": false, "mcp_status": null}}}
{"timestamp": ..., "message": {"type": "TurnEnd", "payload": {}}}
```

Notable wire-only events: `StatusUpdate.plan_mode`, `StatusUpdate.mcp_status`, `ToolCallPart` (streamed argument fragments), `ToolResult.return_value.display[]` (diff/brief renderable hints).

### Tool-call counting

For parity with `CodexResult.tool_call_events`, the adapter should:

1. Skip `--final-message-only` for task/plan invocations.
2. Increment a counter for each NDJSON line where `role == "assistant"` AND `tool_calls` is a non-empty array (count `len(tool_calls)`).
3. Optionally also count `role == "tool"` lines as a sanity cross-check.

For `prose_reply`-style invocations the count is unimportant; `--final-message-only` is fine there.

### MCP tool name shape

Empirical:

- Server alias `toy`, tool `shout(text)` → model called as `shout` (bare).
- Server alias `any_team`, same tool → still called as `shout` (bare).

The wrapper MCP server alias does not need to dodge underscores or any other character class for naming reasons; choose `anyteam` (matches Gemini for cosmetic consistency). The model addresses wrapper tools by their **declared MCP tool names** — `send_message`, `task_update`, etc. — exactly as Codex does, and **not** with a Gemini-style `mcp_anyteam_*` prefix. This is a non-trivial divergence from Gemini and must be reflected in `prompts.py`.

### Wrapper-tool / built-in-tool collision risk

Default agent tool list (loaded from `~/.local/share/uv/tools/kimi-cli/lib/python3.12/site-packages/kimi_cli/agents/default/agent.yaml`):

```text
Agent, AskUserQuestion, SetTodoList, Shell, TaskList, TaskOutput, TaskStop,
ReadFile, ReadMediaFile, Glob, Grep, WriteFile, StrReplaceFile,
SearchWeb, FetchURL, ExitPlanMode, EnterPlanMode
```

PascalCase. The shared wrapper exposes snake_case names (`send_message`, `task_update`, `task_create`, `task_list`, `read_inbox`, `read_config`, plus the Gemini-shadow tools `shell`, `read_file`, `write_file`, `list_directory`, `edit_file`, `search`, `web_fetch`). `task_list` vs `TaskList` are case-distinct and live in different namespaces (built-in vs MCP); collisions are not observable on the wire, but **the prompt must direct the model to the correct snake_case wrapper tool when both exist**.

Recommendation: prompts use the bare snake_case wrapper-tool names (`send_message`, `task_update`, etc.). Gemini-shadow tools (`shell`, `read_file`, etc.) are **redundant** under Kimi because Kimi's built-ins are richer and equivalent. Either drop them from the Kimi wrapper config, or document that they exist for cross-backend symmetry but the Kimi prompt should prefer Kimi built-ins for filesystem/shell work and reserve wrapper tools for the protocol layer.

## Auth and config persistence

Observed user-level Kimi files on this host:

```text
~/.kimi/config.toml                  # default thinking, default model, loop limits, providers, OAuth refs
~/.kimi/kimi.json                    # work_dirs[]: {path, kaos, last_session_id} — used by `-C/--continue`
~/.kimi/device_id                    # device id for telemetry/auth
~/.kimi/latest_version.txt           # update check
~/.kimi/credentials/kimi-code.json   # OAuth tokens, mode 0600
~/.kimi/credentials/kimi-code.lock
~/.kimi/sessions/<md5(cwd)>/<sid>/   # per-session: context.jsonl, wire.jsonl, state.json
~/.kimi/logs/kimi.log                # rotating log
~/.kimi/user-history/                # input line history
~/.kimi/telemetry/
~/.kimi/plans/<random-name>.md       # plan-mode artifacts (created in --plan turns)
```

Implications for adapter isolation:

- Like Gemini, OAuth lives at user scope (`~/.kimi/credentials/kimi-code.json`). If the adapter overrides `HOME` to isolate per-teammate state, OAuth will be invisible unless we copy the credentials file (and its `.lock`) into the isolated home, exactly the pattern `prepare_isolated_gemini_home` uses.
- Sessions are already cwd-isolated via the `<md5(cwd)>` directory level, so two Kimi teammates working in the same project would land in the same hash dir — they would not share session UUIDs, but they would share the work-dir dir. `~/.kimi/kimi.json` is single-file and updated on every run; **two concurrent Kimi adapters writing it in the same cwd would race** — last write wins, which corrupts `-C/--continue` for the other teammate.
- `~/.kimi/config.toml` is read-only from the adapter's perspective; the adapter does not need to mutate it. MCP injection should go through `--mcp-config-file` (adapter-owned, ephemeral path) or `--mcp-config <JSON>` (purely in-argv), not through `~/.kimi/mcp.json`.

Recommended isolation pattern (mirrors `prepare_isolated_gemini_home`):

1. Compute `kimi_home = ~/.cache/claude-anyteam/kimi/<safe_team>/<safe_agent>`.
2. Set `HOME=<kimi_home>` for the kimi child. Kimi's `~/.kimi/` resolves under this isolated `HOME` automatically.
3. Copy `credentials/kimi-code.json`, `credentials/kimi-code.lock`, `device_id`, and `latest_version.txt` from real `$HOME/.kimi/` into `<kimi_home>/.kimi/` on first start; do not symlink (token refresh will re-write the file and we want each adapter's copy to drift safely).
4. Optionally copy `config.toml` if a custom adapter default is desired; otherwise let Kimi initialize with built-in defaults.
5. **Restore real `HOME` in the wrapper-MCP child env** so the wrapper server can access the real `~/.claude/teams/<team>/` config and inbox. Same pattern as Gemini.
6. Sessions/workdir map stay in the isolated home, so `-C/--continue` is per-teammate by construction and the kimi.json race goes away.

## MCP support and discovery

CLI surface:

```text
kimi mcp add <name> [--transport stdio|http] [--env KEY=VALUE]... [--header KEY:VALUE]... [--auth oauth] [-- command args...]
kimi mcp remove <name>
kimi mcp list
kimi mcp test <name>           # active connect probe + tool list
kimi mcp auth <name>           # OAuth flow trigger
kimi mcp reset-auth <name>     # clear cached OAuth
```

Persistent config: `~/.kimi/mcp.json`. Same JSON shape accepted via `--mcp-config-file <FILE>` and `--mcp-config <JSON>`.

Empirical config schema (verified by feeding both `--mcp-config-file` and `--mcp-config` and seeing a successful tool call):

```json
{
  "mcpServers": {
    "anyteam": {
      "command": "/abs/path/to/claude-anyteam-wrapper",
      "args": ["--team", "<team>", "--name", "<agent>"],
      "env": {"HOME": "/home/<user>", "CLAUDE_ANYTEAM_TEAM": "<team>", "CLAUDE_ANYTEAM_NAME": "<agent>"}
    }
  }
}
```

Notes:

- **Top-level key is `mcpServers`** — same as Gemini, same as Claude Code. `claude_anyteam_wrapper` (Codex-style `mcp_servers`) is **not** the right key.
- The schema is the standard MCP "stdio server" object (`command`, `args`, `env`) — no `trust` field is required, no `timeout` field is required; both are accepted by Kimi's TOML/JSON loader without complaint when present but they are not necessary for the wrapper to work in `--print --yolo` mode.
- Empty `env` is fine; Kimi inherits the parent process env then overlays any explicit `env` keys. Important: if `HOME` is overridden on the kimi child for isolation, the wrapper child inherits the **isolated** HOME unless the MCP `env` block restores the real one. Always include `"HOME": "<real_home>"` in the wrapper env block.
- `--mcp-config <JSON>` is repeatable; multiple invocations merge under `mcpServers`. For the adapter, prefer **one** `--mcp-config-file` pointed at an adapter-owned ephemeral file under `<kimi_home>/.kimi/anyteam-mcp.json`; this keeps argv short and lets us re-emit the file without re-spawning kimi.
- Tool-call timeout default is `mcp.client.tool_call_timeout_ms = 60000` (per `~/.kimi/config.toml`). The wrapper's `send_message`/`task_update` calls are well under this.
- **Built-in tool name collision is real but invisible on the wire** — see "Wrapper-tool / built-in-tool collision risk" above.

`kimi mcp test <name>` is a useful adapter feature-test signal. It connects to a configured server and prints the tool list. We can use it as a post-install diagnostic, not as the runtime probe (it requires the config to be persisted to `~/.kimi/mcp.json`, which we do not want for ephemeral adapter configs).

## Session resumption and the auto-create landmine

Verified flows:

- `kimi -r <known-uuid> --print -p ...` resumes the session, retains prior context. Session id stays the same in stderr.
- `kimi -C --print -p ...` resumes whatever id is in `~/.kimi/kimi.json[work_dirs[].path == cwd].last_session_id`. Verified across two turns: token "RAINBOW42" set in turn 1 was correctly recalled in turn 2 with the same session id `c53655e9-657e-4ce7-8d6c-25c6c618c2e9`.
- `kimi -r <unknown-id> --print -p ...` **silently creates a new session named `<unknown-id>`**, exit 0. Log line: `Session no-such-session-id not found, creating new session` then `Resuming session: no-such-session-id`. **Stdout/stderr are indistinguishable from a real resume.**

Adapter consequences:

1. The control loop must not treat exit code 0 + matching session id as proof that resume succeeded. Two safer strategies:
   - **Validate before launch:** `os.path.isdir(kimi_home / ".kimi" / "sessions" / md5(cwd) / session_id)` and only pass `-r/-S` if the directory exists; otherwise drop the resume flag and start fresh. Capture the new id from stderr afterwards.
   - **Use `--continue` exclusively after the first turn:** trust kimi's own `kimi.json` map. The first turn writes the id; subsequent turns use `-C`. This avoids stale ids the adapter might still hold.
2. Session id capture: parse stderr for `^To resume this session: kimi -r ([0-9a-f-]{36})\s*$`. The kimi.json `last_session_id` is a fallback but is racy if multiple Kimi adapters share `HOME` (they shouldn't — see isolation pattern).
3. Cancel/cleanup: there is no `--abort-session` CLI. To force a clean break, the adapter can simply not pass `-r`/`-C` on the next call. The orphan session is harmless: `state.json` retains `archived: false` but no live process holds it.

## App-server analog: `kimi acp`

`kimi acp` starts a JSON-RPC 2.0 ACP server over stdio. `kimi --acp` is deprecated.

Verified `initialize` response (ran `echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{}}}' | timeout 4 kimi acp 2>&1`):

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "agentCapabilities": {
      "loadSession": true,
      "mcpCapabilities": {"http": true, "sse": false},
      "promptCapabilities": {"audio": false, "embeddedContext": true, "image": true},
      "sessionCapabilities": {"list": {}, "resume": {}}
    },
    "agentInfo": {"name": "Kimi Code CLI", "version": "1.39.0"},
    "authMethods": [{
      "_meta": {"terminal-auth": {"command": "/home/rosado/.local/bin/kimi", "args": ["login"], "label": "Kimi Code Login", "env": {}, "type": "terminal"}},
      "description": "Run `kimi login` command in the terminal, then follow the instructions to finish login.",
      "id": "login",
      "name": "Login with Kimi account"
    }],
    "protocolVersion": 1
  }
}
```

Compared to Gemini ACP at the same protocol version:

| Aspect | Gemini 0.39.0 ACP | Kimi 1.39.0 ACP | Implication for adapter |
| --- | --- | --- | --- |
| Protocol version | `1` | `1` | Same wire — `jsonrpc_stdio.py` framing is reusable. |
| `agentInfo` | `{name: "gemini-cli", version: "0.39.0"}` | `{name: "Kimi Code CLI", version: "1.39.0"}` | Trivially different; neither encodes capability negotiation here. |
| `loadSession` | `true` | `true` | Both. |
| `sessionCapabilities` | (none surfaced in research doc) | `list: {}`, `resume: {}` | **Kimi advertises session list/resume; Gemini does not.** Kimi may support `session/list` and `session/resume` JSON-RPC methods that Gemini lacks. Worth probing in Plan B. |
| MCP transports | HTTP + SSE | HTTP only | Kimi cannot consume an SSE-only MCP server over ACP. The wrapper is stdio so this does not affect parity. |
| `authMethods[]` | Vendor-specific (oauth-personal et al.) | Single `login` method invoking `kimi login` in a terminal | Auth-required path different but the `_authenticate_if_required` helper from Gemini ACP can be reused with method-id `"login"`. |
| Stdout JSON-RPC framing pollution | Non-JSON preamble lines (`.geminiignore`, hook init) | **Clean** — no observed preamble before the JSON-RPC reply when stdin closes promptly, only stderr `AuthlibDeprecationWarning`. | Kimi ACP framing is stricter; Gemini's preamble-tolerant parser still works for it. |

Stdout buffering caveat (open question): when `( echo INIT; sleep 8 ) | kimi acp` runs with stdout/stderr separated, **stdout was empty** even after the sleep. With streams merged (`2>&1`) the JSON-RPC response appeared. Repro is reliable across both `2>&1`-then-head and `>file 2>file2` invocations: the latter never produced stdout bytes. This may be a Python stdout-buffer issue in `kimi acp` when the controlling process is not a TTY — pyplot of `python -u`-style unbuffered output is missing. **Plan B implementers must confirm whether `PYTHONUNBUFFERED=1` (or `--debug`) restores stdout flushing**, or whether ACP responses are only flushed when stdin is half-closed and the process is exiting. This is a hard blocker for any naive line-buffered ACP client and was not seen on Gemini.

ACP method names that the Gemini ACP client implements (`initialize`, `authenticate`, `session/new`, `session/load`, `session/prompt`, `session/cancel`, `set_session_mode`, `unstable_set_session_model`) are **plausibly supported by Kimi too** given the protocolVersion match, but only `initialize` was probed. Plan B research must verify each.

## Defaults and configuration knobs

From `~/.kimi/config.toml` (vendor defaults this host inherits):

- `default_model = "kimi-code/kimi-for-coding"` — alias resolves to provider `managed:kimi-code` (`base_url = https://api.kimi.com/coding/v1`, OAuth-backed) and model id `kimi-for-coding` (`display_name = "Kimi-k2.6"`, `max_context_size = 262144`, capabilities: thinking, image_in, video_in).
- `default_thinking = true` — thinking enabled by default; `--no-thinking` opts out per call.
- `default_yolo = false` — interactive mode requires explicit `-y`; `--print` overrides.
- `default_plan_mode = false`.
- `merge_all_available_skills = true` — discovered skills are concatenated into the system prompt automatically.
- `loop_control.max_steps_per_turn = 500`, `max_retries_per_step = 3`, `max_ralph_iterations = 0`, `reserved_context_size = 50000`, `compaction_trigger_ratio = 0.85`.
- `mcp.client.tool_call_timeout_ms = 60000`.
- `background.agent_task_timeout_s = 900`, `print_wait_ceiling_s = 3600`.

Adapter mapping decisions (already in `kimi-build-team-brief.md`, restated here with empirical justification):

| Adapter knob | Kimi flag/setting | Notes |
| --- | --- | --- |
| `effort = minimal\|low` | `--no-thinking` | Lossy. Kimi has no graded thinking budget on this version. |
| `effort = medium\|high\|xhigh` | `--thinking` (default) | Lossy. No `thinkingBudget`/`thinkingLevel` analog like Gemini's. |
| `model = <slug>` | `--model <slug>` (e.g. `kimi-code/kimi-for-coding`) | Slugs are `provider/model` form per `~/.kimi/config.toml [models."..."]`. |
| max-steps cap | `--max-steps-per-turn` | Default 500 is generous; only set if adapter wants tighter parity with Gemini's request loop. |
| approvals | implicit `--yolo` from `--print` | Adapter does not need `-y`. |
| sandbox | n/a | Kimi has no sandbox flag; rely on filesystem permissions and the cwd. |
| trust mode | none | No Gemini-style `--approval-mode plan/default/yolo` triad. The closest analog is `--plan` which auto-exits in headless. |

## Explicit gaps vs Codex/Gemini features this repo relies on

| Codex/Gemini feature | Kimi status | Adapter consequence |
| --- | --- | --- |
| `codex exec --json` JSONL event stream | Per-message NDJSON only | Different parser. No `init`/`result` events on stdout. Session id from stderr regex. |
| Gemini stream-json `init/message/tool_use/tool_result/result` taxonomy | Per-message envelopes (`assistant`/`tool`) only | Tool-call counting works (count `tool_calls[]` arrays), but there is no terminal `result.status` to disambiguate truncated streams from completed ones. Use exit code + presence of a final assistant line with no `tool_calls`. |
| `--output-schema <schema>` | None | Embed schema in prompt, validate with Python jsonschema, retry on failure. Identical pattern to Gemini headless. |
| Codex `--dangerously-bypass-approvals-and-sandbox` | `--print` ⇒ `--yolo` is implicit | No additional flag needed in argv. |
| Codex App Server `turn/start`/`turn/steer` | None at parity | Plan A loop has no mid-turn steering. Plan B (ACP) advertises `loadSession`/`session/list`/`session/resume`/`session/cancel`; `turn/steer` direct equivalent is not visible at `initialize` time. |
| `--resume <id>` semantics | Auto-creates on miss | Adapter must validate the session dir exists OR use `-C` after first turn. See "Session resumption and the auto-create landmine" above. |
| Inline ephemeral MCP config via `-c mcp_servers...` (Codex) | `--mcp-config <JSON>` (Kimi) or `--mcp-config-file <FILE>` | Direct equivalent in argv. Adapter should prefer `--mcp-config-file` pointed at `<kimi_home>/.kimi/anyteam-mcp.json`. |
| Wrapper MCP tools called by **bare snake_case** name (Codex) | Bare snake_case name (Kimi) | Direct match — no `mcp_<server>_<tool>` mangling like Gemini. Kimi prompts mirror Codex prompts more closely than Gemini prompts at the tool-name level. |
| Tool-call telemetry | Per-line `assistant.tool_calls[]` | Sum of array lengths across stream-json lines. |
| Resume session id capture | stderr regex | `^\s*To resume this session: kimi -r ([0-9a-f-]{36})\s*$` |
| User config not mutated by adapter | Achievable | `--mcp-config-file` keeps `~/.kimi/mcp.json` untouched; `HOME` isolation keeps `kimi.json` and sessions untouched. |
| Plan-mode read-only enforcement | Not enforced in `--print` | `--plan` auto-exits and proceeds. Adapter cannot rely on Kimi for read-only planning — must use Codex/Gemini-style schema-only prompt + jsonschema validation, never executing the plan. |

## Open questions for runtime/Plan-B implementation

1. **ACP stdout flushing.** The single biggest open item. Reproduce `( echo init; sleep 8 ) | kimi acp >out 2>err` and determine the buffering rule. If Plan B requires a TTY-backed pty, that is a substantial implementation difference vs Gemini ACP.
2. **`session/list` and `session/resume` JSON-RPC methods.** Kimi advertised them at `initialize`-time; verify they exist and what they accept/return. If `session/resume` is reliable, ACP becomes the cleaner resume path than the headless auto-create landmine.
3. **`session/cancel` mid-turn semantics.** Confirm whether it interrupts a running step, drops a queued tool call, or only cancels-then-replays. Required to claim any mid-turn-steer parity.
4. **`--debug` impact on stdout.** Whether `KIMI_LOG_LEVEL` / `--debug` introduces stderr noise that breaks our regex, or whether it forces stdout to flush (would solve item 1 trivially).
5. **`--input-format stream-json` schema.** Verified `{"role":"user","content":"<text>"}` works. Probe whether multi-message arrays or `tool_result` injections are accepted — would let the adapter feed structured prior context without using `-r`.
6. **MCP `trust`/`timeout` keys.** They are accepted but not required; verify whether they are silently ignored, applied per-server, or rejected by a future Kimi schema validator. Lock the wrapper MCP config to the documented minimum (`command`, `args`, `env`).
7. **Concurrent kimi adapters in the same isolated home.** The chosen isolation pattern eliminates contention by giving every adapter its own `<kimi_home>`, but if a deployment shares HOME for some reason (NFS, container template), `kimi.json` and `mcp.json` writes will race. Document and guard.
8. **Token model and context size.** Defaults read 262 144 tokens / 50 000 reserved / 0.85 compaction trigger. Confirm whether the wrapper's protocol prompts blow through `reserved_context_size` on long-running teams; if so, set `max_steps_per_turn` lower or drive turns shorter.
