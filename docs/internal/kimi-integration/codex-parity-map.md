# Codex parity map for Kimi integration

This is the Kimi counterpart to `docs/internal/gemini-integration/codex-parity-map.md`. It inventories every Codex (and where applicable Gemini) touchpoint in the repo and turns each into an implementable Kimi parity item. Open empirical questions are cross-referenced to `kimi-runtime.md` and the skills/agent/swarm scope decision in `kimi-skill-and-agent-research.md`. Where a runtime question is unresolved, the item flags it explicitly so the runtime/loop work can pick the right cut.

## Implementation stance

- [ ] Add `src/claude_anyteam/backends/kimi/` mirroring `src/claude_anyteam/backends/gemini/`. Do **not** thread Kimi branches through `src/claude_anyteam/codex.py` or `src/claude_anyteam/loop.py`. The Gemini split has settled the abstraction and the per-backend module pattern is the cheap, low-risk path for v1. (`src/claude_anyteam/backends/gemini/{config,invoke,loop,prompts,acp,acp_client,crash_hygiene,cli}.py` is the template; the kimi tree should match shape, not necessarily feature surface.)
- [ ] Keep shared protocol surfaces shared: `protocol_io.py`, `messages.py`, `wrapper_server.py`, `schemas/`, `schema_validation.py`, `registration.py`, `env.py` (with new Kimi-specific env constants), and the spawn shim's routing layer all stay backend-neutral with additive routing/config extensions.
- [ ] Plan A is `kimi --print --output-format stream-json …`. Plan B (`kimi acp`) is **deferred** until the stdout-buffering blocker in `kimi-runtime.md §App-server analog` is closed. v1 ships Plan A only and documents the gap honestly.
- [ ] Empirical-only judgments. Every parity item below either points at a verified Kimi behavior in `kimi-runtime.md` or marks the question open and names the probe that would close it.

## Checklist

### 1. Codex/Gemini adapter module: subprocess invocation, MCP injection, event parsing, result type

- [ ] **Where (Codex):** `src/claude_anyteam/codex.py:1-874`. `CodexResult` (`52-65`), wrapper-tool classification (`68-151`), `wrapper_mcp_config_args` (`154-190`), `feature_test()` (`193-248`), `_probe_wrapper_mcp` (`251-307`), `run()` headless (`310-539`), `app_server_invoke()` (`542-843`), `SteerQueue` (`846-874`).
- [ ] **Where (Gemini template):** `src/claude_anyteam/backends/gemini/invoke.py:1-496` and `acp.py:1-481`.
- [ ] **What Codex does:** Provides the core Codex runtime adapter with `CodexResult` as the loop-facing contract; classifies events from `codex exec --json` and the App Server; injects the wrapper MCP via inline `-c mcp_servers.*`; supports `--output-schema` for hard schema; and offers App Server mid-turn steering.
- [ ] **Kimi equivalent:** Add `src/claude_anyteam/backends/kimi/invoke.py` returning `CodexResult` (or a renamed shared `BackendResult` alias — out of scope for v1; reuse the existing dataclass like Gemini does). Implement:
  - `feature_test(kimi_binary)` — `which kimi`; run `kimi --version` and `kimi --help`; assert presence of `--print`, `--output-format`, `--mcp-config-file`, `--session`/`-r`, `--continue`/`-C`. Optionally probe `kimi info` for protocol metadata; do **not** require a specific protocol version yet (current host: `agent spec versions: 1`, `wire protocol: 1.9`).
  - `run(prompt, *, cwd, schema, kimi_binary, timeout_s, wrapper_identity, resume_session_id, model, effort, kimi_home)` — argv: `[kimi_binary, "--print", "--output-format", "stream-json", "--mcp-config-file", <ephemeral>, …, "-p", prompt]`. Add `-m <model>`, `--no-thinking` for `effort in {minimal,low}`, `--max-steps-per-turn <N>` if a settings cap is set. Resume rule: only pass `-r/-S <id>` if `<kimi_home>/.kimi/sessions/<md5(cwd)>/<id>/` exists; otherwise drop the flag and let the run start fresh (avoid the auto-create landmine — see `kimi-runtime.md §Session resumption and the auto-create landmine`). Capture session id from stderr regex `^\s*To resume this session: kimi -r ([0-9a-f-]{36})\s*$`. `stdin=DEVNULL` unless `--input-format stream-json` is opted in.
  - Stream-json parser: per-line `json.loads`; tolerate non-JSON terminators on stdout (e.g. `Max number of steps reached: 1`). Build `last_message` by concatenating `text` parts of the **last** assistant line (the one with no `tool_calls`). Count `tool_call_events` as `sum(len(line["tool_calls"]) for line in lines if line.get("role") == "assistant" and isinstance(line.get("tool_calls"), list))`. On `--final-message-only`, `content` is a plain string and `tool_calls` is absent — adapter should either skip `--final-message-only` for task/plan invocations (preferred) or branch the parser.
  - Schema validation: prompt-side embed via `claude_anyteam.schema_validation.inline_schema_prompt_fragment`; Python `parse_and_validate(_extract_json_candidate(last_message), load_schema(schema))`. Mirror Gemini's tolerant code-fence stripper.
  - Error/timeout: `subprocess.TimeoutExpired` → `CodexResult(exit_code=124, error="kimi timed out…")`; nonzero exit → embed stderr tail + first non-JSON stdout line in `error`.
- [ ] **Hard Kimi dependency:** ACP runtime methods (Plan B). Plan A is fully empirically grounded. See open question in `kimi-runtime.md §Open questions` for the stdout-buffering blocker.

### 2. Codex App Server JSON-RPC client is Codex-specific

- [ ] **Where:** `src/claude_anyteam/app_server.py:1-510`.
- [ ] **What Codex does:** Owns `codex app-server` subprocess, JSON-RPC dispatch, and Codex-specific helpers (`thread/start`, `turn/start`, `turn/steer`, `turn/interrupt`, `thread/fork`, `thread/read`).
- [ ] **Kimi equivalent:** Do not reuse `app_server.py` for Plan A. For Plan B, reuse the **transport layer** (`gemini/acp_client.py:GeminiAcpClient` + the JSON-RPC framing it inherits from `jsonrpc_stdio.py`) but prove out Kimi method names first. The Kimi `initialize` response advertises:
  - `protocolVersion: 1` — same as Gemini.
  - `sessionCapabilities.list: {}`, `sessionCapabilities.resume: {}` — Kimi exposes session list/resume that Gemini does not advertise. Likely method names: `session/list`, `session/resume`. Probe before assuming.
  - `loadSession: true` — supports `session/load` like Gemini.
  - `mcpCapabilities: {http: true, sse: false}` — wrapper is stdio-MCP so unaffected.
  - `authMethods[0].id = "login"` — terminal auth via `kimi login`.
  - **Stdout-buffer caveat:** `kimi acp` may not flush stdout when stdin stays open, breaking line-buffered ACP clients. Reproducer in `kimi-runtime.md §App-server analog`. **This is the v1 blocker for Plan B.**
- [ ] **Hard Kimi dependency:** ACP method-name probing and the stdout-flush behavior. v1 ships Plan A only.

### 3. Control loop hard-calls Codex and stores Codex session state

- [ ] **Where (Codex):** `src/claude_anyteam/loop.py:1-854`. Imports `claude_anyteam.codex as codex_mod`; tracks `codex_session_id` and `app_server_last_thread_id`; dispatches between App Server, `codex exec resume`, fresh `codex exec`; implements App Server inbox steering.
- [ ] **Where (Gemini template):** `src/claude_anyteam/backends/gemini/loop.py:1-501` — the cleanest model for the Kimi loop because it is one-shot per turn with no App Server branch.
- [ ] **Kimi equivalent:** Add `src/claude_anyteam/backends/kimi/loop.py` copied from Gemini's loop with substitutions:
  - `KimiSettings` injection; `kimi_session_id` (single string) replacing `gemini_session_id`.
  - `_backend_feature_test` calls `kimi_invoke.feature_test(settings.kimi_binary)`.
  - `_backend_run` always headless (no Plan B branch in v1).
  - `register(... BackendMetadata(model="kimi-cli", prompt="Kimi teammate adapter…"))`. Settle on `model="kimi-cli"` so per-teammate config and skill/help text can match on a stable label, regardless of the per-call `--model` slug.
  - Active form `Running kimi on task #{id}`; logger keys prefixed with `kimi.…` matching the Gemini convention.
  - Session policy: prefer `-C/--continue` after the first turn (kimi tracks `~/.kimi/kimi.json[work_dirs[].last_session_id]` per cwd, and we already isolate `HOME` per teammate so the file is private). Fall back to passing `-r/-S <session_id>` only when the directory exists; otherwise drop the resume flag. Drop the session id whenever a permission failure or stop-reason cancel occurs (Gemini's `_should_drop_session_after_failure` shape applies).
  - Plan-mode behavior: do **not** rely on `--plan` for read-only enforcement (kimi auto-exits in headless and proceeds to execute — see `kimi-runtime.md §Capability matrix`). Instead, always use the schema-only prompt path, mirror Gemini's `_handle_plan_approval`, and never set `--plan` from the loop.
  - Steer queue: keep Gemini's `QueuedSteer` shape — only injected as a prefix on the next prompt, no real mid-turn interruption. Document that mid-turn steering is unsupported on the Kimi headless backend.
- [ ] **Hard Kimi dependency:** None for Plan A.

### 4. Runtime configuration is Codex-named but partly backend-neutral

- [ ] **Where:** `src/claude_anyteam/config.py:37-137`; `src/claude_anyteam/env.py:12-64`; `src/claude_anyteam/cli.py:27-85`,`251-294`.
- [ ] **What Codex does:** `Settings` includes Codex knobs; CLI exposes Codex-specific flags; `env.py` keeps legacy Codex env names alive.
- [ ] **Kimi equivalent:** Add `src/claude_anyteam/backends/kimi/config.py` with `KimiSettings` analogous to `GeminiSettings`:
  ```python
  CLAUDE_ANYTEAM_KIMI_BINARY      # default "kimi"
  CLAUDE_ANYTEAM_KIMI_HOME        # adapter-owned HOME for kimi child
  CLAUDE_ANYTEAM_KIMI_BACKEND     # "headless" | "acp", default "headless"
  CLAUDE_ANYTEAM_KIMI_THINKING    # "on" | "off" (rarely used; effort drives this)
  CLAUDE_ANYTEAM_KIMI_MAX_STEPS   # int, optional cap on --max-steps-per-turn
  ```
  Reuse shared `CLAUDE_ANYTEAM_TEAM/NAME/CWD/POLL/COLOR/MODEL`. Keep `effort` in the dataclass for parity with Gemini, but document the lossy mapping:
  ```
  effort ∈ {minimal, low}            → --no-thinking
  effort ∈ {medium, high, xhigh}     → --thinking (default; do not pass)
  ```
  Do NOT reuse `CODEX_BINARY`, `GEMINI_BINARY`, or the legacy `CODEX_TEAMMATE_*` envs. Explicitly set `legacy_*` to `None` in `KimiSettings.from_env`.
- [ ] **Hard Kimi dependency:** None — `kimi-runtime.md §Defaults and configuration knobs` covers the model and effort mapping.

### 5. Prompts mention Codex and bare MCP tool names

- [ ] **Where:** `src/claude_anyteam/prompts.py` (Codex), `src/claude_anyteam/backends/gemini/prompts.py` (Gemini), `src/claude_anyteam/schema_validation.py`.
- [ ] **What Codex does:** Codex prompts use **bare** wrapper-tool names (`send_message`, `task_update`, etc.). Gemini prompts use `mcp_anyteam_<tool>` because Gemini transforms MCP tool names. Schema validation prompt fragments are shared.
- [ ] **Kimi equivalent:** Add `src/claude_anyteam/backends/kimi/prompts.py`. Use **bare** wrapper-tool names exactly like Codex, **not** the Gemini-style `mcp_anyteam_*`. Empirically verified in `kimi-runtime.md §MCP tool name shape`: Kimi exposes MCP tools by their declared name with no server-prefix mangling, even when the server alias contains underscores. Prompts must:
  - Address the model as a Kimi CLI teammate ("You are {agent_name}, a Kimi CLI teammate on the {team_name} team.").
  - Mention bare wrapper tools `send_message`, `task_update`, `task_create`, `read_inbox`, `task_list`, `read_config`.
  - Either drop the Gemini shadow tools (`shell`, `read_file`, `write_file`, `list_directory`, `edit_file`, `search`, `web_fetch`) from the wrapper config Kimi gets, **or** keep them and tell the model in the prompt to prefer Kimi's built-ins (`Shell`, `WriteFile`, `StrReplaceFile`, `Glob`, `Grep`, `ReadFile`, `FetchURL`, `SearchWeb`) for filesystem/shell work and reserve wrapper tools for the protocol layer. Recommended v1 default: drop the shadow tools from the Kimi wrapper config because they are redundant under Kimi's built-ins and add prompt clutter.
  - Always embed schemas via `schema_validation.inline_schema_prompt_fragment()`, exactly like Gemini, since Kimi has no `--output-schema`.
  - Do **not** instruct the model to "draft a plan in non-execution mode" via `--plan` — that flag does not enforce read-only in headless. Use the same plan-prompt pattern Gemini does: schema-constrained planning prompt + Python validation, never call `--plan`.
- [ ] **Hard Kimi dependency:** None.

### 6. MCP wrapper server is shared, but identity/config injection is Codex-shaped at call sites

- [ ] **Where:** `src/claude_anyteam/wrapper_server.py:1-334`; Codex `src/claude_anyteam/codex.py:154-190`,`675-692`,`251-307`; Gemini `src/claude_anyteam/backends/gemini/invoke.py:315-346` (`write_mcp_settings`).
- [ ] **What Codex does:** Wrapper server exposes the team-protocol tools. Codex injects via inline `-c mcp_servers.claude_anyteam_wrapper.…` overrides; Gemini writes an adapter-owned `.gemini/settings.json` with `mcpServers.anyteam.{command,args,env,trust,timeout}` and restores real `HOME` in the wrapper child.
- [ ] **Kimi equivalent:** Reuse `wrapper_server.py` unchanged. Write an adapter-owned ephemeral file at `<kimi_home>/.kimi/anyteam-mcp.json`:
  ```json
  {
    "mcpServers": {
      "anyteam": {
        "command": "<absolute path to claude-anyteam-wrapper>",
        "args": ["--team", "<team>", "--name", "<agent>"],
        "env": {
          "HOME": "<real_home>",
          "CLAUDE_ANYTEAM_TEAM": "<team>",
          "CLAUDE_ANYTEAM_NAME": "<agent>",
          "CODEX_TEAMMATE_TEAM": "<team>",
          "CODEX_TEAMMATE_NAME": "<agent>"
        }
      }
    }
  }
  ```
  Pass it via `--mcp-config-file <path>` (one flag, one file). Use `anyteam` as the alias for cosmetic consistency with Gemini; underscores in aliases work too but offer no benefit (`kimi-runtime.md §MCP tool name shape`). Do not include `trust` or `timeout` keys — they are accepted but unnecessary in `--print --yolo` mode and should be omitted to track the documented minimum schema.
  - Restoring real `HOME` in the wrapper env is mandatory — without it the wrapper would read the isolated kimi `HOME` and fail to find `~/.claude/teams/<team>/`.
  - Wrapper binary path must be absolute (`shutil.which("claude-anyteam-wrapper")` like Gemini's `_wrapper_binary`). Copy the Gemini helper as-is.
  - Mutating `~/.kimi/mcp.json` is **forbidden** for the same reason Gemini avoids `~/.gemini/settings.json`. The user's persistent MCP configuration is read-only from the adapter's perspective.
- [ ] **Hard Kimi dependency:** None.

### 7. MCP probe hardcodes `python`

- [ ] **Where:** `src/claude_anyteam/codex.py:251-307` (`_probe_wrapper_mcp`), specifically `282-288` where `["python", "-c", …]` is hardcoded. Per `~/.claude/projects/.../memory/MEMORY.md` this is a known landmine on this host (the project shim already mitigates by prepending a wrapper to PATH, but the adapter source is still fragile).
- [ ] **What Codex does:** Startup probe runs `python -c 'from claude_anyteam.wrapper_server import build_server; build_server(); print("OK")'` to validate that the wrapper server module imports.
- [ ] **Kimi equivalent:** Do **not** copy this pattern into the Kimi adapter. Two cleaner options:
  1. Probe the resolved `claude-anyteam-wrapper` binary directly with `--help` or a `--version`-style flag, with `subprocess.run([wrapper_binary, "--version"], …)`. (Gemini's `feature_test` uses this style.)
  2. If a Python import probe is genuinely needed, use `[sys.executable, "-c", "import claude_anyteam.wrapper_server; print('OK')"]`.
- [ ] **Hard Kimi dependency:** None — repo-side correctness.

### 8. Registration hardcodes Codex/Gemini member metadata

- [ ] **Where:** `src/claude_anyteam/registration.py:84-160` (already accepts `BackendMetadata`); deregistration `165-215`.
- [ ] **What Codex/Gemini do:** `register()` writes a member entry under `~/.claude/teams/{team}/config.json` with `agentType`, `model`, and `prompt` from the supplied `BackendMetadata`. Codex defaults to `model="codex-cli"`. Gemini's loop passes `BackendMetadata(model="gemini-cli", prompt="Gemini teammate adapter…")`.
- [ ] **Kimi equivalent:** Loop calls `register(settings, BackendMetadata(model="kimi-cli", prompt="Kimi teammate adapter. Protocol I/O is handled by the adapter; coding work is delegated to Kimi CLI headless mode. No Claude LLM is involved."))`. Keep `agentType="claude-anyteam"` and `backendType="in-process"`. **Do not** put a per-call `--model` slug like `kimi-code/kimi-for-coding` in the registry `model` field — that field is the stable backend identifier the help skill matches on. Per-teammate `agents/<name>.json` already covers per-call slug overrides via the spawn shim's `_AGENT_CONFIG_KEYS = ("model", "effort")`.
- [ ] **Hard Kimi dependency:** None.

### 9. TUI presence depends on Claude Code spawn path, not config.json

- [ ] **Where:** `docs/architecture.md:57-72`; `src/claude_anyteam/installer.py` (`teammateMode="tmux"` setup); `src/claude_anyteam/spawn_shim.py`.
- [ ] **What Codex/Gemini do:** Installer sets `teammateMode="tmux"` so Claude Code uses the pane backend; spawn shim is invoked by Claude Code's teammate spawn flow, which populates in-memory `AppState.tasks`. Self-registration in `config.json` alone is insufficient for TUI presence.
- [ ] **Kimi equivalent:** Routes `kimi-*` names through the same spawn shim and pane backend. No separate strategy is required; this is an architectural property of Claude Code, not the backend. Update `docs/architecture.md` to mention `kimi-*` alongside `codex-*` and `gemini-*`. Tests in `tests/test_skills.py` and `tests/test_plugin_bundle.py` already exercise the prefix patterns and should grow `kimi-*` cases.
- [ ] **Hard Kimi dependency:** None.

### 10. Spawn shim routes only `^codex-` and `^gemini-` names

- [ ] **Where:** `src/claude_anyteam/spawn_shim.py:1-298` (current state already has `_codex_route` / `_gemini_route`); env constants `src/claude_anyteam/env.py`.
- [ ] **What Codex/Gemini do:** `DEFAULT_CODEX_MATCH = r"^codex-"`, `DEFAULT_GEMINI_MATCH = r"^gemini-"`; per-route `_resolve_binary` lookups; both routes share `_adapter_argv` which forwards `--name`, `--team`, `--plan-mode`, plus per-agent `model`+`effort` from `~/.claude/teams/<team>/agents/<name>.json` (whitelist `_AGENT_CONFIG_KEYS = ("model", "effort")`).
- [ ] **Kimi equivalent:** Add:
  - `DEFAULT_KIMI_MATCH = r"^kimi-"` and `KIMI_BINARY = "kimi-anyteam"` constants.
  - `KIMI_BINARY_ENV = "CLAUDE_ANYTEAM_KIMI_BINARY"` (env override) and `KIMI_SHIM_MATCH_ENV = "CLAUDE_ANYTEAM_KIMI_SHIM_MATCH"` in `env.py`.
  - `_kimi_route(parsed)` returning `_route_match(parsed, env_name=KIMI_SHIM_MATCH_ENV, legacy_env_name=None, default=DEFAULT_KIMI_MATCH)`.
  - Routing branch in `main` after the gemini branch:
    ```python
    if _kimi_route(parsed):
        binary = _require_binary(_resolve_binary(KIMI_BINARY, KIMI_BINARY_ENV), KIMI_BINARY)
        adapter_argv, agent_config = _adapter_argv(binary, parsed, include_effort=True)
        _log_dispatch("kimi", parsed.agent_name, binary, agent_config or None)
        os.execv(binary, adapter_argv)
    ```
  - `include_effort=True` because the adapter consumes `--effort` and translates internally to `--thinking`/`--no-thinking`.
- [ ] **Hard Kimi dependency:** Adapter binary name (`kimi-anyteam`) must match the pyproject entry-point (item 12 below).

### 11. Installer writes a single shim and checks Codex/Gemini CLI prereqs

- [ ] **Where:** `src/claude_anyteam/installer.py:1-2132` — `CodexCliCheck`, `GeminiCliCheck` (line 90), `MANAGED_BINARY_KEYS`, `MANAGED_BINARY_BASENAMES` (47-50), `_check_gemini_cli`, `_check_gemini_signin`, etc.
- [ ] **What Codex/Gemini do:** Install writes `CLAUDE_CODE_TEAMMATE_COMMAND=<claude-anyteam-spawn-shim>`, `CLAUDE_ANYTEAM_BINARY`, and `CLAUDE_ANYTEAM_GEMINI_BINARY` into `~/.claude/settings.json`; sets `teammateMode="tmux"` in `~/.claude.json`; runs informational nonblocking CLI version checks for Codex (≥0.120.0) and Gemini (`--acp`/`--prompt`/`--output-format`/`--resume`/`--approval-mode` capability set).
- [ ] **Kimi equivalent:** Add `KimiCliCheck` analogous to `GeminiCliCheck`. Probe via:
  - `kimi --version` → parse `kimi, version <X.Y.Z>` (current host: `1.39.0`). Pin a soft minimum at the version verified in `kimi-runtime.md` (1.39.0); below this, install warns but does not block.
  - `kimi info` for protocol metadata (`agent spec versions`, `wire protocol`). Useful as advisory output.
  - `kimi --help` capability check for `--print`, `--output-format`, `--mcp-config-file`, `--session`, `--continue`. Mirror `_GEMINI_REQUIRED_CAPABILITIES`.
  - Auth probe: presence of `~/.kimi/credentials/kimi-code.json` (mode 0600). Empty/missing → installer prints `Run kimi login to authenticate.`
  - Add install state keys `kimi_cli_found`, `kimi_cli_version` to mirror the Gemini state keys.
- [ ] Update `MANAGED_BINARY_KEYS` to include `KIMI_TEAMMATE_BINARY_KEY = "CLAUDE_ANYTEAM_KIMI_BINARY"` and `MANAGED_BINARY_BASENAMES` to include `"kimi-anyteam"`/`"claude-anyteam-kimi"`.
- [ ] Allowlist Bash patterns analogous to the Gemini ones (`Bash(setsid nohup uv run kimi-anyteam *)`, `Bash(pkill -f kimi-anyteam *)`).
- [ ] Do **not** make Kimi missing block Codex/Gemini installs. Treat it as informational, like Codex's check.
- [ ] **Hard Kimi dependency:** Authoritative install command and version floor. The brief says `kimi` is installed via `uv tool install kimi-cli` on this host; capture that in the installer's missing-binary hint.

### 12. Package entry points and legacy script names

- [ ] **Where:** `pyproject.toml:35-38` — `gemini-anyteam = "claude_anyteam.backends.gemini.cli:main"`, `claude-anyteam-gemini = "claude_anyteam.backends.gemini.cli:main"`; bin wrappers `bin/gemini-anyteam`, `bin/claude-anyteam-gemini`.
- [ ] **What Codex/Gemini do:** Two console scripts per backend (one PascalSeparated, one prefixed). Description in `pyproject.toml` mentions Codex + Gemini.
- [ ] **Kimi equivalent:** Add:
  ```toml
  kimi-anyteam = "claude_anyteam.backends.kimi.cli:main"
  claude-anyteam-kimi = "claude_anyteam.backends.kimi.cli:main"
  ```
  Add `bin/kimi-anyteam` and `bin/claude-anyteam-kimi` wrapper scripts mirroring the Gemini ones if the repo's `bin/` directory is part of the plugin packaging (it is — it ships in `.claude-plugin`). Update `description` in `pyproject.toml` to mention Kimi as a third first-class backend.
- [ ] **Hard Kimi dependency:** None.

### 13. Protocol completion message still names `codex_exit_code`

- [ ] **Where:** `src/claude_anyteam/messages.py`, `src/claude_anyteam/protocol_io.py`; Gemini call site `backends/gemini/loop.py:488`; tests `tests/test_messages.py:111-117`.
- [ ] **What Codex/Gemini do:** Both backends pass their process exit code through the existing `codex_exit_code` field on `task_complete` for backwards compatibility. Gemini explicitly comments this is "Backwards-compatible protocol field name; see limitations doc."
- [ ] **Kimi equivalent:** Same pattern. Reuse `codex_exit_code` for the Kimi process exit code in v1; do not rename in this PR series. If a future PR generalizes to `backend_exit_code`, do it in a single sweep with all three backends, not as a Kimi-only break. Document the choice in `docs/internal/kimi-integration/repo-integration-parity-audit.md` once the loop is implemented.
- [ ] **Hard Kimi dependency:** None.

### 14. Per-agent config currently forwards only model/effort

- [ ] **Where:** `src/claude_anyteam/spawn_shim.py:100-153,247-252` (`_AGENT_CONFIG_KEYS = ("model", "effort")` whitelist).
- [ ] **What Codex/Gemini do:** Read `~/.claude/teams/{team}/agents/{agent}.json` and forward `model` + `effort`. Both backends consume `--model` and `--effort` on their adapter CLI.
- [ ] **Kimi equivalent:** Reuse the same whitelist. The Kimi adapter must accept `--model` (passed to `kimi -m`) and `--effort` (translated to `--thinking`/`--no-thinking`). Document model slug shape in `docs/configuration.md` — Kimi expects `provider/model` form per `~/.kimi/config.toml [models."..."]`, e.g. `kimi-code/kimi-for-coding`. Do not whitelist Kimi-specific keys (`thinking`, `agent`, `agent_file`, `skills_dir`) in the spawn-shim path for v1; those belong in env vars or future per-agent extensions, and the skills/agents/swarm doc already declares them advanced configuration only.
- [ ] **Hard Kimi dependency:** None.

### 15. Settings/config files specific to Codex/Gemini and legacy names

- [ ] **Where:** `src/claude_anyteam/env.py:12-64`; backend configs `config.py` modules; `logger.py` prefix conventions; docs `docs/configuration.md`.
- [ ] **What Codex/Gemini do:** Codex honors `CODEX_BINARY` and legacy `CODEX_TEAMMATE_*`; Gemini introduces `CLAUDE_ANYTEAM_GEMINI_*` envs and isolates `~/.gemini/`; both write logs via the shared logger.
- [ ] **Kimi equivalent:** Add `CLAUDE_ANYTEAM_KIMI_BINARY`, `CLAUDE_ANYTEAM_KIMI_HOME`, `CLAUDE_ANYTEAM_KIMI_BACKEND`, `CLAUDE_ANYTEAM_KIMI_THINKING`, `CLAUDE_ANYTEAM_KIMI_MAX_STEPS` to `env.py` (and add `CLAUDE_ANYTEAM_KIMI_SHIM_MATCH` for the shim). Adapter-owned Kimi home defaults to `~/.cache/claude-anyteam/kimi/<safe_team>/<safe_agent>/.kimi/`. Sessions, work_dirs map, plans, telemetry, and credentials all live under that root and never under the user's `~/.kimi/`. Document in `docs/configuration.md` that:
  - The user's real `~/.kimi/credentials/kimi-code.json` is **copied** (not symlinked) into the adapter home on first run, mirroring the Gemini auth-cache copy pattern.
  - `~/.kimi/config.toml` is read-only from the adapter; user model/provider edits propagate by being copied into adapter home, or — preferred for v1 — **not copied** so the adapter uses Kimi's built-in defaults and stays insulated from user customization.
  - `kimi.json` (`work_dirs[].last_session_id`) is per-teammate by virtue of HOME isolation; concurrent Kimi adapters do not race.
  - Logger uses `kimi.…` keys (e.g. `kimi.invoke`, `kimi.tool_call`, `kimi.session.dropped`, `kimi.loop.start`).
- [ ] **Hard Kimi dependency:** Whether `config.toml` should be copied or omitted from isolation. v1 default: omit, document the rationale, add `CLAUDE_ANYTEAM_KIMI_COPY_USER_CONFIG=1` opt-in only if a user demands it.

### 16. Package-level branding still assumes Codex

- [ ] **Where:** `src/claude_anyteam/__init__.py`; `pyproject.toml:1-46`.
- [ ] **What Codex/Gemini do:** Package metadata describes Codex- and Gemini-powered teammates.
- [ ] **Kimi equivalent:** Update package description to "Codex, Gemini, and Kimi-backed teammates for Claude Code Agent Teams" or similar. Do not rename the import path (`claude_anyteam`) for Kimi; backends live under `claude_anyteam.backends.kimi`.
- [ ] **Hard Kimi dependency:** None.

### 17. Dev/probe helpers are Codex-only unless explicitly expanded

- [ ] **Where:** `src/claude_anyteam/plan_probe.py`, `src/claude_anyteam/shutdown_probe.py`, `src/claude_anyteam/roundtrip_m1.py`.
- [ ] **What Codex/Gemini do:** Codex-specific developer probes; Gemini did not gain matching probes. Documented as Codex-only.
- [ ] **Kimi equivalent:** Treat as non-goals for v1. If smoke probes are added in a follow-up, name them `kimi_*` to keep them obviously Kimi-specific (don't overload Codex defaults). Mention in `repo-integration-parity-audit.md` once the loop is in place.
- [ ] **Hard Kimi dependency:** None.

### 18. Shared JSON schemas have Codex teammate labels

- [ ] **Where:** `schemas/plan.schema.json`, `schemas/task-complete.schema.json`, `schemas/permission_request.schema.json`, `schemas/permission_response.schema.json`.
- [ ] **What Codex/Gemini do:** Schema shapes are protocol contracts; titles/descriptions still embed "Codex teammate" labels and bleed into prompt fragments and validation errors.
- [ ] **Kimi equivalent:** Reuse schema shapes unchanged. Audit the title/description strings during prompt assembly; in `kimi/prompts.py`, render via the same `inline_schema_prompt_fragment()` helper but make sure error retry text says `Kimi` rather than `Codex`. If easier, add a small helper that swaps labels at prompt time. Do not edit the schema files for Kimi-specific wording; that's a cross-backend cleanup task.
- [ ] **Hard Kimi dependency:** None.

### 19. Hook/plugin orientation text teaches only Codex/Gemini routing

- [ ] **Where:** `hooks/session-start.sh:5-74`; `.claude-plugin/marketplace.json:14-21`; `.claude-plugin/plugin.json:1-14`; tests `tests/test_plugin_bundle.py:17-20,44-57`.
- [ ] **What Codex/Gemini do:** Session-start hook prints orientation listing both routes; manifests describe both prefix patterns.
- [ ] **Kimi equivalent:** Extend `hooks/session-start.sh` to mention `kimi-*` alongside `codex-*` and `gemini-*` (architectural-stretch tasks, large context, native skill discovery, swarm-internal fan-out). Update marketplace + plugin manifests to mention Kimi. Keep hook settings detection logic unchanged unless new env keys require validation. Update `tests/test_plugin_bundle.py` and `tests/test_skills.py` assertions.
- [ ] **Hard Kimi dependency:** None.

### 20. User-facing docs are Codex/Gemini-first

- [ ] **Where:** `README.md`; `docs/architecture.md`; `docs/roadmap.md`; `docs/configuration.md`; `docs/install.md`; `skills/help/SKILL.md`; `skills/status/SKILL.md`; `npm/README.md`.
- [ ] **What Codex/Gemini do:** Doc set teaches `codex-*` and `gemini-*` prefixes, install/auth, model catalogs, mixed-team examples, and limitations.
- [ ] **Kimi equivalent:** After implementation lands, update each doc to describe three first-class backends:
  - **README.md:** add Kimi to the value prop and feature matrix; quickstart for `kimi-*`.
  - **architecture.md:** mention Kimi alongside Codex App Server / Gemini headless and describe Plan A as the v1 path; ACP deferred.
  - **configuration.md:** Kimi env vars (`CLAUDE_ANYTEAM_KIMI_*`), per-agent JSON keys, model slug shape (`provider/model`), effort→thinking mapping, isolated HOME, MCP file path.
  - **install.md:** Kimi CLI prereq + `kimi login` hint, version floor, optional `uv tool install kimi-cli` instruction.
  - **roadmap.md:** mark Kimi v1 shipped; ACP deferred with reasons.
  - **skills/help/SKILL.md:** teach Claude when to choose `kimi-*` (architectural-stretch tasks, large context, swarm workflows), regex `^kimi-`, per-teammate config shape with example slug `kimi-code/kimi-for-coding`, mixed-team examples.
  - **skills/status/SKILL.md:** include Kimi backend status if applicable.
  - **npm/README.md:** extend success copy to include `kimi-*`.
- [ ] **Hard Kimi dependency:** Runtime limitations from `kimi-runtime.md` (especially Plan B / mid-turn steering / plan-mode-not-enforced).

### 21. Tests specific to Codex/Gemini adapters need Kimi counterparts

- [ ] **Where:** Codex tests: `tests/test_codex_event_matching.py`, `tests/test_codex_invocation_shape.py`, `tests/test_codex_mcp_config.py`, `tests/test_app_server_*`. Gemini tests: `tests/test_gemini_*` (the brief lists the categories — verify in tree). Cross-cutting: `tests/test_messages.py`, `tests/test_schema_validation.py`, `tests/test_wrapper_contract.py`, `tests/test_registration*.py`, `tests/test_spawn_shim.py`, `tests/test_skills.py`, `tests/test_plugin_bundle.py`, `tests/test_install_command.py`.
- [ ] **What Codex/Gemini do:** Guard argv shape, MCP config shape, event parser, registration metadata, spawn-shim routing, install warnings, prompt/schema retry behavior, per-agent config plumbing, and orientation-text invariants.
- [ ] **Kimi equivalent (per Phase 4 of the brief):**
  - `tests/test_kimi_invocation_shape.py` — argv must include `--print --output-format stream-json`, `-m <model>` when set, `--no-thinking` when effort∈{minimal,low}, `--mcp-config-file <ephemeral>`, `-r <session>` only when session dir exists, `stdin=DEVNULL`, no `--input-format`.
  - `tests/test_kimi_invoke.py` — feed fixture stream-json transcripts (NDJSON-of-messages, not Gemini-style event stream), assert `last_message`, `tool_call_events`, `session_id` capture from stderr, exit-code propagation including the Max-steps stdout-non-JSON case, schema-validation retry shape.
  - `tests/test_kimi_mcp_config.py` — assert MCP file shape (top-level `mcpServers`, alias `anyteam`, args `--team`/`--name`, env restores real `HOME`, wrapper command absolute), and that `~/.kimi/mcp.json` is **never** mutated.
  - `tests/test_kimi_effort.py` — `effort∈{minimal,low}` ⇒ `--no-thinking`; `effort∈{medium,high,xhigh}` ⇒ no `--thinking` flag added (default-on); unknown effort raises.
  - `tests/test_kimi_registration.py` — registry entry has `model="kimi-cli"`, `agentType="claude-anyteam"`, `backendType="in-process"`, prompt mentions Kimi.
  - `tests/test_kimi_loop_session_policy.py` — first-turn no resume, second-turn uses `-C/--continue`, invalid stored session id is dropped (do not pass `-r` when dir does not exist), permission/cancel drops session.
  - `tests/test_kimi_plan_approval.py` — plan-mode flow uses schema-only prompt, never sets `--plan` (regression test against the auto-exit landmine), retry on invalid JSON.
  - `tests/test_spawn_shim.py` — extend with `^kimi-` route cases without breaking codex/gemini cases; verify env override `CLAUDE_ANYTEAM_KIMI_BINARY` and `CLAUDE_ANYTEAM_KIMI_SHIM_MATCH`.
  - `tests/test_skills.py`, `tests/test_plugin_bundle.py` — assert Kimi appears in hook/plugin/skill text alongside Codex/Gemini.
  - `tests/test_install_command.py` — extend with `KimiCliCheck` smoke (informational, non-blocking, missing CLI must not fail install).
  - **Run full pytest at end** — must pass with the existing 201 tests plus new Kimi tests, baseline `pytest -m "not integration"`. No regressions on Codex/Gemini paths.
- [ ] **Hard Kimi dependency:** Stream-json fixture transcripts. Capture them by re-running the probes from `kimi-runtime.md` and checking the raw NDJSON into `tests/fixtures/kimi/`.

### 22. Existing planning/feasibility docs already define approved constraints

- [ ] **Where:** `docs/internal/kimi-build-team-brief.md`, `docs/internal/kimi-rationale.md`, `docs/internal/strategic-roadmap.md` (Phase 1 trigger), `docs/internal/kimi-integration/kimi-runtime.md`, `docs/internal/kimi-integration/kimi-skill-and-agent-research.md`. Sister docs from the Gemini effort: `docs/gemini-adapter-feasibility.md`, `docs/internal/gemini-plans.md`, `docs/internal/gemini-research-official.md`, `docs/internal/gemini-research-reverse.md`, `docs/internal/gemini-integration/repo-integration-parity-audit.md`.
- [ ] **What Codex/Gemini do:** Establish the implementation stance, scope, and parity bar. The Gemini integration's `repo-integration-parity-audit.md` is the post-implementation reconciliation between plan and shipped state.
- [ ] **Kimi equivalent:** Treat the four Kimi integration docs (`kimi-build-team-brief.md`, `kimi-runtime.md`, `kimi-skill-and-agent-research.md`, this file) as the authoritative constraints. After the loop and tests land, write `docs/internal/kimi-integration/repo-integration-parity-audit.md` reconciling planned vs shipped, including:
  - Which of the 22 items are implemented, deferred, or N/A.
  - Plan A vs Plan B status; ACP blocker note.
  - Stream-json parser quirks discovered during implementation.
  - Any drift from this map and the runtime doc.
- [ ] **Hard Kimi dependency:** None — this is the closing-the-loop deliverable in Phase 5.

## Suggested implementation order

Mirrors the Phase 0–4 plan in `kimi-build-team-brief.md`:

1. **Phase 1 runtime module** (codex-runtime, blocked by this map and `kimi-runtime.md`):
   - `backends/kimi/__init__.py`, `config.py`, `invoke.py`, `prompts.py`.
   - Tests: `tests/test_kimi_invocation_shape.py`, `tests/test_kimi_invoke.py`, `tests/test_kimi_mcp_config.py`, `tests/test_kimi_effort.py`.
2. **Phase 2 control loop and CLI** (codex-loop): `backends/kimi/loop.py`, `backends/kimi/cli.py`. Tests: `tests/test_kimi_loop_session_policy.py`, `tests/test_kimi_plan_approval.py`, `tests/test_kimi_registration.py`.
3. **Phase 3 repo integration** (codex-installer-docs): `spawn_shim.py` extension, `pyproject.toml`, `installer.py` (`KimiCliCheck`), `env.py` constants, `bin/` scripts, `hooks/session-start.sh`, manifests, skills, README + docs. Tests: `tests/test_spawn_shim.py` extension, `tests/test_skills.py`, `tests/test_plugin_bundle.py`, `tests/test_install_command.py`.
4. **Phase 4 cross-cutting tests** (codex-tests): full `pytest -m "not integration"` run; reconcile any failures introduced by the parity changes; add a limitations doc for the Plan B / mid-turn steering / plan-mode-not-enforced gaps.
5. **Phase 5 audit and PR prep**: `repo-integration-parity-audit.md` + PR description + branch cleanup.

## Open dependencies for runtime/loop work

- ACP stdout-buffer behavior — blocker for Plan B, deferred to v2.
- Session id capture rule under `--continue` plus `-r` simultaneously (Kimi help does not document precedence). Probe before relying on combined flags. v1 design uses one or the other, never both.
- Whether `--debug` produces stderr noise that breaks the `To resume this session` regex; if yes, document the env hint to omit it.
- Cross-backend `BackendResult` rename (out of scope here; track in repo-integration-parity-audit follow-up).
