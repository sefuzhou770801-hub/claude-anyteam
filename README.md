# codex-teammate

An OpenAI Codex CLI process acting as a first-class teammate inside Claude
Code's agent-team protocol — without wrapping Codex inside a Claude LLM.

## Status

Feasibility prototype. See `docs/architecture-decision.md` for the design
rationale and `docs/protocol-spec.md` for the team protocol contract.

## How it works

The adapter is a long-running Python process that:

1. Self-registers with a running Claude Code team by appending its entry to
   `~/.claude/teams/{team}/config.json`. M0 confirmed the harness tolerates
   this mutation and routes messages to an externally-created inbox file.
2. Polls its inbox and the shared task list through direct function calls
   into `cs50victor/claude-code-teams-mcp`, used as a Python library
   (no MCP subprocess).
3. Delegates coding work to `codex exec --json --output-schema` as a
   subprocess. No Claude LLM runs on behalf of the Codex teammate.
4. Responds to `shutdown_request` and, under the opt-in `planModeRequired`
   path, generates structured plans via Codex.

### Why one process, one language

An earlier design had a TypeScript/Node adapter talking to cs50victor's
Python MCP server over stdio. That version worked (the smoke test in
`scrap-ts/` demonstrates it), but once we realised cs50victor was already
Python we collapsed the stdio boundary into an in-process function call.
A single `read_config` round-trip dropped from roughly 45 ms (MCP over
stdio subprocess) to about 4 ms (direct import). The bilingual install
story also disappeared. If productization ever makes a single static
binary worthwhile, porting cs50victor to TS is a clean follow-up; for
a feasibility prototype, the Python-only path is strictly simpler.

## Requirements

- Python 3.12+
- `uv` (for dependency management)
- OpenAI Codex CLI (`codex exec` available on PATH)
- A running Claude Code team at `~/.claude/teams/{team}/`

## Install (development)

```bash
uv sync
uv run codex-teammate --help
```

## Run

### Background launch (canonical)

The adapter runs as a fully-detached background process. This is the
only verified launch path — see the TUI note below.

If you want to run the adapter as a detached background process instead, it
must be **fully detached** from the launching shell — otherwise a SIGHUP
when the shell exits will kill it mid-task and orphan the claimed task in
`in_progress` state. Use `setsid nohup … & disown` with stdin redirected
from `/dev/null`:

```bash
setsid nohup uv run codex-teammate \
  --team my-team \
  --name codex-alice \
  --cwd /path/to/workspace \
  </dev/null \
  >/tmp/codex-alice.stdout \
  2>/tmp/codex-alice.stderr \
  & disown
```

A bare `uv run codex-teammate &` is **not** sufficient — the shell's
process group receives SIGHUP on exit and pulls the adapter down with it.
This was observed live during M2 development and recorded as the
`setsid nohup … & disown </dev/null` incantation above.

### TUI visibility

The external-launch adapter self-registers in `config.json` and receives
inbox messages, but does **not** appear in the Claude Code TUI presence
line (`@main @name`). The TUI renders from the leader's in-memory
`AppState.tasks`, which is only populated by the leader's spawn flow —
not by external self-registration.

`codex-teammate-spawn-shim` (installed as a console script) is designed
to intercept the `CLAUDE_CODE_TEAMMATE_COMMAND` hook and route
`codex-*` names to this adapter rather than to the native Claude binary.
That hook is invoked by Claude Code's **out-of-process external spawn
path** (used in tmux/iTerm2 agent-teams mode). It is **not** invoked by
the Agent tool's in-process sub-agent spawn, which is the default spawn
mechanism in interactive Claude Code sessions.

In practice: if you are using the Agent tool to create teammates (the
common case), the shim is not the right surface. If you are using Claude
Code in tmux/out-of-process agent-teams mode, set
`CLAUDE_CODE_TEAMMATE_COMMAND` in `~/.claude/settings.json` or your
shell and name your Codex teammates with a `codex-` prefix.

See `docs/shim-restart-resilience.md` for the three deferred approaches
to make the shim (or an alternative) work more broadly.

Environment variables (equivalent to the CLI flags):

- `CODEX_TEAMMATE_TEAM` — team name (required; matches the directory under `~/.claude/teams/`)
- `CODEX_TEAMMATE_NAME` — unique teammate name within the team (required)
- `CODEX_TEAMMATE_CWD` — working directory for Codex invocations (default: current)
- `CODEX_TEAMMATE_POLL_S` — inbox poll interval, seconds (default: 1.5)
- `CODEX_TEAMMATE_COLOR` — display color (default: cyan)
- `CODEX_TEAMMATE_PLAN_MODE` — set to `true` to opt into plan mode (default: false)
- `CODEX_TEAMMATE_APP_SERVER` — set to `false` to opt out of App Server mode (default: true; v7.1 mid-task reactivity on)
- `CODEX_TEAMMATE_MODEL` — Codex model slug (e.g. `gpt-5.4`, `gpt-5.3-codex`). When unset, Codex's `~/.codex/config.toml` default applies.
- `CODEX_TEAMMATE_EFFORT` — reasoning effort (`low` | `medium` | `high` | `xhigh`). When unset, Codex's per-model default applies.
- `CODEX_TEAMMATE_LOG` — log level: `debug`, `info`, `warn`, `error` (default: info)
- `CODEX_BINARY` — Codex CLI binary name (default: `codex`)

## Execution modes (App Server vs. fresh-exec)

The adapter supports two Codex invocation shapes. Since v7.1, **App Server
mode is the default**; the fresh-exec path is opt-out.

- **App Server mode (default).** Codex runs as a long-lived JSON-RPC
  session via `codex app-server`. The adapter can inject mid-task input
  into an in-flight turn via `turn/steer` when a teammate sends a
  message during task execution, and as of v7.3 it also carries
  cross-task memory forward by forking from the prior task's thread via
  `thread/fork`. Startup cost per task is low (thread creation is
  ~10-100 ms). See `docs/v7.1-notes.md` and
  `docs/v7.3-implementation-notes.md`.
- **Fresh-exec mode (opt out via `--no-app-server` or
  `CODEX_TEAMMATE_APP_SERVER=false`).** Each task invokes `codex exec`
  fresh. On the second and subsequent tasks for the same adapter
  identity, the adapter invokes `codex exec resume <session_id>` so
  Codex carries prior-task context forward (v7.2 cross-task session
  memory). Validated in Python via `jsonschema`. See
  `docs/v7.2-notes.md`.

The two modes are no longer orthogonal feature profiles:

- **App Server (default)** gives you mid-task reactivity **and**
  cross-task memory, using `turn/steer` plus `thread/fork`.
- **Fresh-exec (`--no-app-server`)** still gives you cross-task memory,
  but via `codex exec resume`, and still lacks mid-task reactivity.

So the choice is now mostly about implementation path and operational
preference, not about whether cross-task memory exists at all. App Server
is the richer default. `--no-app-server` remains useful when you
specifically want the v7.2 fresh-exec/resume behavior.

## Codex sandbox

The adapter invokes `codex exec` with
`--dangerously-bypass-approvals-and-sandbox`. This disables Codex's
sandbox entirely and skips all approval prompts. Two reasons:

1. **Operator-run, in the user's trust envelope.** The adapter is not a
   shared service — it's a tool the user launches themselves against
   their own project. Codex operates in the user's own trust envelope,
   same as when they run `codex exec` directly. The sandbox was adding
   friction without adding security in this context.
2. **The wrapper MCP server inherits Codex's sandbox.** Codex's `--sandbox
   workspace-write` (or `--full-auto`) scopes writes to the `-C` working
   directory only. But the wrapper, spawned by Codex as a subprocess to
   service MCP tool calls, inherits that sandbox — which means
   `task_update`, `send_message`, and the rest silently fail because
   they write to `~/.claude/tasks/` and `~/.claude/teams/*/inboxes/`,
   outside `cwd`. Disabling the sandbox at the invocation level is the
   cleanest way to avoid that transitive restriction.

Codex's own help text describes the bypass flag as "intended solely for
running in environments that are externally sandboxed." An operator-run
Codex teammate is effectively that: externally sandboxed by the
operator's choice of what to run it against.

No entry in `~/.codex/config.toml`'s trusted-projects list is required;
the adapter controls the flag itself per invocation.

## Design choices worth knowing

These are choices that read as oversights until you understand the
reasoning — calling them out explicitly so a future reader doesn't
"fix" them.

**The adapter does not critique Codex's output.** If Codex exits 0 and
produces a schema-conformant `task_complete` response, the adapter marks
the task `completed` and delivers the response to the lead, *even if
`files_changed` is empty when the task description implied file
creation*. Reasoning: semantic validation ("did Codex actually do what
the task asked?") is the lead's job, not the adapter's. Adding that
layer would require the adapter to reason about the task description
against Codex's output, which is exactly the LLM-layer judgment this
whole project avoids. Observed in M2: Codex's first run couldn't write
files (sandbox was read-only by default), honestly reported "the write
was blocked" in the summary, and the lead rejected the completion and
reset the task. That cycle is the intended control flow — adapter
relays, lead validates, lead re-assigns if needed.

**Empty-string `owner` is treated as unassigned.** Some tools that reset
a task's owner serialize the field as `""` instead of `null`. The
adapter's claim filter treats both as "unowned" so a reset task flows
into the unassigned-pending queue correctly.

**`deregister()` deletes the inbox file.** Symmetric with `register()`,
which creates it. A stale inbox file left after shutdown would
accumulate across re-runs of the same teammate name. Best-effort —
an I/O error during deletion doesn't block the config deregistration.

**Plan mode is inbound-triggered, not spontaneous.** When
`planModeRequired=True`, the adapter reacts to an inbound
`plan_approval_request` message by invoking Codex once with
`--output-schema plan.schema.json` and sending the structured plan back.
It does **not** spontaneously emit `plan_approval_request` when it claims
a task. The adapter is reactive in the plan-mode sense: the lead asks
for a plan, the adapter produces one via Codex. Per architecture §4.5
a spontaneous/pre-execution trigger is also valid and can be added in
a future refactor if needed.

## Opt-in plan mode

Launch with `--plan-mode` (or `CODEX_TEAMMATE_PLAN_MODE=true`) to register
with `planModeRequired: true`:

```bash
setsid nohup uv run codex-teammate \
  --team my-team \
  --name codex-planner \
  --cwd /path/to/workspace \
  --plan-mode \
  </dev/null >/tmp/codex-planner.stdout 2>/tmp/codex-planner.stderr \
  & disown
```

Then send a `plan_approval_request` to it. The bundled probe helps:

```bash
uv run python -m codex_teammate.plan_probe codex-planner 8
#                                          ^target       ^optional task id
```

The adapter will run `codex exec --json --output-schema plan.schema.json`
once, then reply to team-lead with a `plan_approval_request` carrying
the structured plan. If Codex fails to produce a schema-conformant
response, the adapter retries once with a tightened prompt. Two failures
in a row cause the target task to be marked `blocked` — no canned stub
is ever sent (per §4.5 policy).

## License

MIT
