# Kimi repo-integration parity audit

Date: 2026-04-25  
Reviewer: kimi-architect  
Scope: parity-map completeness and repo-integration implementation audit against `docs/internal/kimi-integration/codex-parity-map.md`. This is the closing-the-loop deliverable for the `feat/kimi-adapter` PR series; it walks every parity-map point and marks **shipped** / **partial / deviation** / **deferred-with-rationale** / **N/A** with merge SHAs and the empirical evidence cited.

This audit was written while Tasks #17 (docs/skills/plugin/npm) and #18 (full kimi test suite) were still in flight. The §"Still in flight" section calls out what the audit must be refreshed against once those land.

## Summary verdict

**Plan A (`kimi --print --output-format stream-json`) is implementation-complete.** The runtime adapter, control loop, CLI entry point, spawn-shim routing, installer probe, and a focused regression test pass cleanly against the empirical findings in `kimi-runtime.md`. 7 of 8 `team-lead` critical findings are correctly handled; #5 (wrapper boundary) ships with an accepted prompt-discipline-only mitigation that requires a follow-up shared-module change. Plan B (`kimi acp`) is deferred behind the documented stdout-buffering blocker.

**Pre-merge gaps that block PR readiness, owned by Task #17 + #18:** package entry points (`pyproject.toml`), `bin/` wrappers, hook/plugin manifests, README, docs/architecture/configuration/install/roadmap, npm bootstrap, and the full kimi test category suite (only `test_kimi_invocation_shape.py` ships in this audit's snapshot).

The 22 parity-map points are individually scored below; the per-point status maps to one of the four labels above.

## Empirical-truth-keeper review against runtime findings

These eight findings were called out by `team-lead` as the critical correctness checks against `kimi-runtime.md`. Verified against the merged code (`d27c636 invoke.py`, `4b3a50d prompts.py`, the loop and cli scaffolds at `a67d3b5`):

| # | Finding | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Resume `-r <id>` validates session dir before passing through (no silent auto-create trust) | **PASS** | `invoke.py:391-394` defines `_known_session()` checking `<kimi_home>/.kimi/sessions/<md5(cwd)>/<id>/` is a directory. `_run_once` lines 451-455 only emit `--session <id>` after that check; on miss, logs `kimi.resume_session_missing` and starts fresh. Avoids the `kimi-runtime.md §Session resumption and the auto-create landmine` trap. |
| 2 | `--plan` flag NOT used (plan-mode is schema-only) | **PASS** | `invoke.py:439-456` argv builder never adds `--plan`. `loop.py:_handle_plan_approval` at lines 297-300 uses `prompts.plan_prompt()` + `inline_schema_prompt_fragment()` and validates the JSON output, mirroring the Gemini schema-only path. |
| 3 | MCP tool names BARE (no `mcp_<server>_<tool>` mangling) in prompts | **PASS** | `prompts.py:11-17` lists `send_message(...)`, `task_update(...)`, `task_create(...)`, `read_inbox(...)`, `task_list()`, `read_config()` — all bare snake_case. No `mcp_anyteam_*` prefix. Matches Codex prompt style and `kimi-runtime.md §MCP tool name shape` empirical claim. |
| 4 | `kimi acp` not used (Plan B deferred) | **PASS** | `loop.py:84-87` `_backend_feature_test` raises `NotImplementedError("Plan B deferred to follow-up PR")` when `settings.backend == "acp"`. Same guard at `loop.py:100-101` in `_backend_run`. `kimi acp` subprocess never spawns. |
| 5 | Wrapper exposes only the 6 team-protocol tools, no Gemini-shadow tools | **PARTIAL / DEVIATION** | The shared `wrapper_server.py:54-67` `EXPOSED_TOOLS` list still contains all 13 tools (6 protocol + 7 `mcp_anyteam_*` shadow tools). The wrapper accepts no per-invocation `--enabled-tools` or `--protocol-only` flag (`wrapper_server.py:84-104` only parses `--team`/`--name`). The Kimi adapter mitigates by **prompt discipline only** — `prompts.py:24` mentions only the 6 bare protocol tools and tells the model to use Kimi built-ins (`Shell`, `WriteFile`, etc.) for execution. The model **could** still call the shadow tools if it ignored the prompt. Matches option (B) from `kimi-runtime.md §Wrapper-tool / built-in-tool collision risk`. **Follow-up:** add a `--protocol-only` flag to `wrapper_server.py` (shared-module change requiring team-lead consensus per the brief) so Kimi can hard-restrict the boundary instead of relying on prompt discipline. |
| 6 | `--final-message-only` not used in default argv (preserves tool-call lifecycle for counting) | **PASS** | `invoke.py:439-456` argv has no `--final-message-only`. The default argv preserves the per-message NDJSON the parser counts in `_parse_stdout`. Backed by research probe `tests/fixtures/kimi/_research_probes/final_message_only_with_tool.jsonl`. |
| 7 | `json.loads(arguments)` wrapped in try/except for the invalid-JSON-mid-emission case | **PASS** | `invoke.py:319-340` `_validate_tool_call_arguments` wraps `json.loads(arguments)` in `try/except json.JSONDecodeError`, logs `kimi.tool_call_arguments_invalid`, and refuses to count the malformed call toward `tool_call_events`. Backed by research probe `tests/fixtures/kimi/_research_probes/max_steps_overflow.jsonl` showing the truncated `"{\"command"` payload. |
| 8 | Non-JSON terminator lines tolerated on stdout | **PASS** | `invoke.py:347-354` `_parse_stdout` calls `_loads_json_line(line)`; on `JSONDecodeError`, logs `kimi.nonjson_stdout` and records `{"type": "non_json_stdout", "line": line}` in events without crashing the parse. Handles both the contract `stdout_noise.jsonl` (verbose-banner case) and the research-probe `max_steps_overflow.jsonl` (limit-terminator case). |

### Code smell flagged during review

- `invoke.py:29-36` defines `WRAPPER_TOOL_NAMES = frozenset({...})` listing the 6 protocol tools, but the constant is **never referenced anywhere** in the module or tests. It is the literal name list a `--protocol-only` filter would consume; possibly the unfinished half of the finding-#5 mitigation. Suggested cleanup as part of the follow-up: either wire it through to a wrapper filter (preferred) or delete the dead constant.

## Per-point status against `codex-parity-map.md`

### Status legend

- **Shipped:** behavior implemented, evidence cited.
- **Partial / deviation:** core behavior shipped but with a documented gap from the parity-map intent; follow-up needed.
- **Deferred:** intentionally deferred for v1; rationale + follow-up issue.
- **In flight:** owned by Task #17 (docs/skills/plugin/npm) or Task #18 (test suite) at audit time; will be re-graded once those land.
- **N/A:** not applicable to Kimi for v1.

| # | Parity-map point | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Adapter module: subprocess invocation, MCP injection, event parsing, result type | **Shipped** | `src/claude_anyteam/backends/kimi/invoke.py` (565 lines, **d27c636**). Returns `CodexResult`. `feature_test()` covers binary, version, required flags, and runs a headless smoke turn. `run()` argv: `--print --output-format=stream-json --work-dir <cwd> --mcp-config-file <ephemeral> [--no-thinking] [--model] [--session]` with `stdin=DEVNULL`. Stream-json parser handles per-message NDJSON, tolerates non-JSON lines, captures session id from stderr regex, schema-validates final text via `inline_schema_prompt_fragment` + `parse_and_validate`, retries once on schema failure. |
| 2 | Codex App Server JSON-RPC client (`turn/steer`) | **Deferred** | Plan B is `kimi acp`. Blocker documented in `kimi-runtime.md §App-server analog: kimi acp` (stdout buffering when stdin stays open). `loop.py:84-87,100-101` rejects `backend=="acp"` with `NotImplementedError`. No mid-turn steering in v1. Follow-up: probe `PYTHONUNBUFFERED=1` and TTY-allocation; only then implement an ACP client (likely reusing `gemini/jsonrpc_stdio.py` framing — protocol version matches at 1). |
| 3 | Control loop hard-calls Codex and stores Codex session state | **Shipped** | `src/claude_anyteam/backends/kimi/loop.py` (471 lines, **a67d3b5** scaffold + later wiring). `KimiLoopState.kimi_session_id`. `_backend_feature_test`, `_backend_run`, `_main_loop`, `_handle_message`, `_handle_steer`/`_steer_prefix_for_task` (next-turn steer queue), `_handle_prose`, `_handle_shutdown`, `_handle_plan_approval`, `_execute_task`, `_mark_blocked` all mirror the Gemini loop shape with Kimi labels and session policy. Active form: `Running kimi on task #{id}`. |
| 4 | Runtime configuration is Codex-named but partly backend-neutral | **Shipped** | `src/claude_anyteam/backends/kimi/config.py` (87 lines, **4697b66**). `KimiSettings` dataclass with `kimi_binary`, `model`, `effort`, `kimi_home`, `backend`, `thinking`. `from_env()` reads `CLAUDE_ANYTEAM_TEAM/NAME/CWD/POLL/COLOR/MODEL/EFFORT` shared envs plus `CLAUDE_ANYTEAM_KIMI_BINARY/HOME/BACKEND/THINKING` Kimi-specific envs. `KIMI_EFFORTS = {minimal, low, medium, high, xhigh}`. No reuse of `CODEX_BINARY` or Gemini envs. |
| 5 | Prompts mention Codex and bare MCP tool names | **Shipped** | `src/claude_anyteam/backends/kimi/prompts.py` (47 lines, **57870be** + **4b3a50d**). `task_prompt`, `prose_reply_prompt`, `plan_prompt` all address the model as "a Kimi CLI teammate on the {team_name} team" and use bare snake_case tool names. Schema validation prompt fragment embedded by the loop, not the prompt builder. Gemini-shadow tools NOT mentioned in any prompt — model is steered to Kimi built-ins for execution work. |
| 6 | MCP wrapper server: shared, identity/config injection | **Partial / deviation** | Wrapper itself is reused unchanged (good). Adapter writes `<kimi_home>/.kimi/anyteam-mcp.json` via `invoke.write_mcp_config()` with `mcpServers.anyteam.{command, args, env}` (`invoke.py:179-210`). Wrapper command resolves via `shutil.which("claude-anyteam-wrapper")` with `sys.executable -m claude_anyteam.wrapper_server` fallback. Real `HOME` restored in wrapper env. **Deviation:** the wrapper still exposes all 13 tools to the Kimi child; the Kimi prompt only mentions the 6 protocol tools. See "Empirical-truth-keeper review" finding #5 above for the rationale and the `--protocol-only` follow-up. |
| 7 | MCP probe hardcodes `python` | **Shipped** | `invoke.py:172-176` `_wrapper_command_args()` uses `shutil.which()` for the wrapper binary and falls back to `sys.executable` (not the literal string `"python"`). Sidesteps the Codex `python -c …` landmine documented in `MEMORY.md:project_codex_teammate` / `feedback_python_shim.md`. |
| 8 | Registration hardcodes Codex member metadata | **Shipped** | `loop.py:50-58` calls `register(settings, BackendMetadata(model="kimi-cli", prompt="Kimi teammate adapter. Protocol I/O is handled by the adapter; coding work is delegated to Kimi CLI headless mode. No Claude LLM is involved."))`. Default `agentType="claude-anyteam"`, `backendType="in-process"`. The model field is the stable backend label, not the per-call model slug — matches parity-map item #8. |
| 9 | TUI presence depends on Claude Code spawn path, not config.json | **Shipped** | `kimi-*` names route through the same spawn shim (`spawn_shim.py:283-288`) and pane backend; no separate strategy invented. Tested in `tests/test_spawn_shim.py` (extension at **51a5f18**). |
| 10 | Spawn shim routes only `^codex-`/`^gemini-` names | **Shipped** | `spawn_shim.py:25-31,109-114,209-211,283-288` (commits **a84cad3** + **3138adf** + **b9fad35**). Adds `DEFAULT_KIMI_MATCH = r"^kimi-"`, `KIMI_BINARY = "kimi-anyteam"`, `_kimi_route()` helper, dispatch branch, route label `"kimi"`, env override `CLAUDE_ANYTEAM_KIMI_BINARY` and `CLAUDE_ANYTEAM_KIMI_SHIM_MATCH`. `_AGENT_CONFIG_KEYS = ("model", "effort")` whitelist preserved. Tests in `tests/test_spawn_shim.py` extension cover route hit, env override, conflict-with-gemini cases. |
| 11 | Installer writes a single shim and checks Codex/Gemini CLI prereqs | **Shipped** | `src/claude_anyteam/installer.py` (commits **8e5a391** + **7756065**). Adds `KimiCliCheck` dataclass (line 124), `KIMI_CLI_BINARY = "kimi"`, install command `"uv tool install --python 3.13 kimi-cli"`, curl install fallback, `KIMI_CLI_DOCS_URL`, `KIMI_CREDENTIALS_PATH = .kimi/credentials/kimi-code.json`, `_check_kimi_signin()` (line 1014), `_check_kimi_cli()` (line 1173). `MANAGED_BINARY_KEYS` includes `KIMI_TEAMMATE_BINARY_KEY`; `MANAGED_BINARY_BASENAMES` includes `"kimi-anyteam"`/`"claude-anyteam-kimi"`. Bash allowlist includes `Bash(setsid nohup uv run kimi-anyteam *)` and `Bash(pkill -f kimi-anyteam *)`. `provider_key` Literal extended to include `"kimi"`. Installer Kimi check is informational, non-blocking. |
| 12 | Package entry points and legacy script names | **In flight (Task #17)** | `pyproject.toml` does **not** yet contain `kimi-anyteam` / `claude-anyteam-kimi` console scripts; `bin/kimi-anyteam` / `bin/claude-anyteam-kimi` shell wrappers do **not** yet exist. Without these, the spawn shim's `_resolve_binary("kimi-anyteam", KIMI_BINARY_ENV)` call resolves only via `CLAUDE_ANYTEAM_KIMI_BINARY` env override — there is no PATH-resolvable default. **Hard requirement before merge.** Re-grade once Task #17 lands. |
| 13 | Protocol completion message still names `codex_exit_code` | **Shipped (intentional carry-over)** | `loop.py:460` calls `pio.send_task_complete(..., codex_exit_code=result.exit_code)` with a comment `# Backwards-compatible protocol field name; see limitations doc.` Same pattern as Gemini's loop. Renaming to `backend_exit_code` is deferred to a future cross-backend sweep. |
| 14 | Per-agent config currently forwards only model/effort | **Shipped** | Spawn shim's `_AGENT_CONFIG_KEYS = ("model", "effort")` whitelist is unchanged; `_adapter_argv(... include_effort=True)` for Kimi (`spawn_shim.py:283-288`). Kimi's CLI accepts `--model` (forwarded to `kimi -m`) and `--effort` (translated to `--no-thinking` for `effort∈{minimal,low}`, default thinking otherwise). Per-agent JSON shape `{"model": "kimi-code/kimi-for-coding", "effort": "xhigh"}` documented in `skills/help/SKILL.md`. |
| 15 | Settings/config files specific to Codex/Gemini and legacy names | **Shipped** | Adapter-owned home defaults to `~/.cache/claude-anyteam/kimi/<safe_team>/<safe_agent>/` (`invoke.py:47-49`, `default_kimi_home`). `prepare_isolated_kimi_home` copies `kimi-code.json`, `kimi-code.lock`, AND `config.toml` (`invoke.py:83-100`). **Minor deviation:** `config.toml` is copied, where my runtime-doc recommendation was "v1 default: omit" so the adapter is insulated from user customization. Acceptable v1 choice — preserves user model/provider customization at the cost of insulation. Document in user docs. Logger uses `kimi.…` keys (`kimi.invoke`, `kimi.tool_call`, `kimi.signal.received`, `kimi.loop.start`, etc.). |
| 16 | Package-level branding still assumes Codex | **In flight (Task #17)** | `pyproject.toml` description not yet updated for Kimi. `README.md:7` still reads "Codex and Gemini today. Kimi, GLM, DeepSeek next" and `README.md:105` shows "⏳ Kimi adapter" in the feature matrix. `src/claude_anyteam/__init__.py` description not yet updated. Re-grade once Task #17 lands. |
| 17 | Dev/probe helpers are Codex-only | **N/A** | Per parity map: explicit non-goal for v1. No `kimi_*` probe helpers added. Documented stance unchanged. |
| 18 | Shared JSON schemas have Codex teammate labels | **Shipped (no change required)** | Schemas reused unchanged. Kimi prompts use `inline_schema_prompt_fragment` + same retry text patterns as Gemini. Schema title/description "Codex teammate" wording remains a cross-backend cleanup item that none of the three backends has solved. Out of scope for this PR. |
| 19 | Hook/plugin orientation text teaches only Codex/Gemini routing | **In flight (Task #17)** | `hooks/session-start.sh` does **not** yet mention `kimi-*`. `.claude-plugin/marketplace.json` and `.claude-plugin/plugin.json` do **not** yet mention Kimi. `tests/test_plugin_bundle.py` and `tests/test_skills.py` do not yet assert Kimi presence. Re-grade once Task #17 lands. |
| 20 | User-facing docs are Codex/Gemini-first | **In flight (Task #17)** — partial | `skills/help/SKILL.md` is updated for Kimi (lines 4, 13, 21, 25-26, 39, 46, 68, 73, 81 — `^kimi-` regex, when-to-choose guidance, model slug example, mixed-team example) — **shipped**. `docs/architecture.md` has only one passing reference (line 98). `docs/configuration.md`, `docs/install.md`, and `docs/roadmap.md` (line 25 lists Kimi as "Next") all need Kimi sections. `docs/roadmap.md:30` explains the Kimi-first sequencing rationale — partial. `skills/status/SKILL.md` not yet updated. `npm/README.md` not yet updated. Re-grade once Task #17 lands. |
| 21 | Tests specific to Codex/Gemini adapters need Kimi counterparts | **In flight (Task #18)** — partial | Shipped: `tests/test_kimi_invocation_shape.py` (commit at **a67d3b5** subset; argv shape, schema embedding, MCP config writer assertions); `tests/test_spawn_shim.py` extension (`^kimi-` route, env overrides, conflict cases, **51a5f18**); `tests/test_install_command.py` extension for Kimi prereq (**0adc8af**, +404 lines). All 130 tests passing on this audit's snapshot (`pytest tests/test_kimi_invocation_shape.py tests/test_spawn_shim.py tests/test_install_command.py -q` → `130 passed in 1.27s`). NOT yet shipped: `tests/test_kimi_invoke.py`, `tests/test_kimi_mcp_config.py`, `tests/test_kimi_effort.py`, `tests/test_kimi_registration.py`, `tests/test_kimi_loop_session_policy.py`, `tests/test_kimi_plan_approval.py`, `tests/test_kimi_prompts.py`, `tests/test_kimi_signin.py`, plus skills/plugin assertions for Kimi. Re-grade once Task #18 lands. |
| 22 | Existing planning/feasibility docs already define approved constraints | **Shipped** | All four Phase-0 planning docs are on the branch: `kimi-build-team-brief.md` (**34c6087**), `kimi-runtime.md` (**c0761d0** + sign-in addendum at **55f9530**), `kimi-skill-and-agent-research.md` (**569be05**), `codex-parity-map.md` (**2f75c66**). This audit (`repo-integration-parity-audit.md`) closes the loop. |

## What is implemented against the parity checklist

Quick affirmative inventory mirroring the Gemini audit:

- **Backend/package entrypoints (Plan A only):** `src/claude_anyteam/backends/kimi/` exists and is imported by Plan A. `pyproject.toml` console scripts and `bin/` wrappers still pending under Task #17.
- **Kimi runtime module:** `cli.py` (47 lines), `config.py` (87 lines), `invoke.py` (565 lines), `loop.py` (471 lines), `prompts.py` (47 lines), `__init__.py` (empty).
- **MCP wrapper reuse and alias:** adapter-owned `<kimi_home>/.kimi/anyteam-mcp.json`; alias `anyteam`; identity env restoration; `--mcp-config-file` only (never `--mcp-config <inline>`).
- **Registration metadata:** `BackendMetadata(model="kimi-cli", prompt="Kimi teammate adapter…")`.
- **Spawn routing/TUI path:** `^kimi-` regex, dispatch label `kimi`, env override `CLAUDE_ANYTEAM_KIMI_BINARY`, model+effort forwarded.
- **Installer/package wiring:** `KimiCliCheck`, `_check_kimi_signin`, install command `uv tool install --python 3.13 kimi-cli`, curl-based fallback, docs URL, sign-in artifact path. `MANAGED_BINARY_KEYS` widened.
- **Schemas/prompts:** schemas reused unchanged; Kimi prompts say "Kimi CLI teammate" and use bare snake_case wrapper-tool names.
- **Help skill:** `skills/help/SKILL.md` updated with `^kimi-` regex, when-to-choose guidance, model slug examples, lossy effort mapping, mixed-team examples.
- **Tests:** focused regression tests for invocation argv shape, spawn-shim routing, and installer prereq pass green.
- **Empirical evidence:** 12 contract-named stream-json fixtures + 3 off-contract research probes + per-fixture workspaces and an `mcp/anyteam-wrapper.json` template.

## Silently skipped or partially completed parity items

### 1. User-facing docs lag shipped state (Task #17 in flight)

Same shape as Gemini's audit §1, but earlier in the PR cycle: code is in place but the user-facing surface still reads as if Kimi were "next" rather than shipped Plan A.

- `README.md:7` "Codex and Gemini today. Kimi, GLM, DeepSeek next" — must move Kimi from "next" to "today".
- `README.md:105` "⏳ Kimi adapter" — must change to ✅ for Plan A.
- `docs/roadmap.md:25` lists Kimi as "Next"; the rationale at line 30 explains the sequencing but the table needs to flip to shipped/partial.
- `docs/architecture.md` only references Kimi in passing (line 98); diagram, per-task flow, and shim regex sections all need Kimi treatment matching the Gemini sections.
- `docs/configuration.md`, `docs/install.md` — no Kimi sections at all yet.
- `skills/status/SKILL.md` — not yet updated.
- `npm/README.md` — not yet updated.

This block lands with Task #17.

### 2. Wrapper-boundary tool exposure is prompt-discipline-only (deviation #5)

The Kimi adapter exposes the full 13-tool wrapper to the model and steers via prompt. The model could in principle ignore the prompt and call `mcp_anyteam_shell` etc. directly. There is no MCP-level enforcement that only the 6 protocol tools are reachable from a Kimi turn.

The follow-up that closes this cleanly is a `--protocol-only` (or `--enabled-tools`) flag on `wrapper_server.py` that the Kimi adapter passes by default. That is a **shared-module change** and per the brief requires team-lead consensus — it should not be slipped in alongside the v1 PR. Recommended: file as a follow-up issue with the `WRAPPER_TOOL_NAMES` constant in `invoke.py` cited as the half-finished mitigation.

### 3. Installer Kimi check is presence/version + sign-in only

`_check_kimi_cli()` runs `kimi --version` and parses the output; `_check_kimi_signin()` validates `~/.kimi/credentials/kimi-code.json` exists, has nonzero size, and parses as JSON. Both are correct. Not yet covered:

- `kimi info` is not used for capability surface assertions (protocol/agent-spec versions). `feature_test()` in `invoke.py:230-247` does call `kimi info` and asserts the help text contains the required flags, so the runtime probe is stronger than the installer probe.
- Headless smoke test exists in `feature_test()` but not at install time. Acceptable — same as Gemini.

This is the same pattern as Gemini's audit §3 and is not a hard blocker.

### 4. `kimi-*` config copy includes `config.toml` where the runtime doc suggested omitting it

`prepare_isolated_kimi_home` copies `~/.kimi/config.toml` along with credentials (`invoke.py:98`). My runtime doc recommended *not* copying it as a v1 default to insulate the adapter from user customization. The shipped behavior is the more permissive choice — user model/provider customization carries through into the adapter home. Acceptable; just document it in `docs/configuration.md` once Task #17 lands so users know the user-level `config.toml` is honored per teammate.

### 5. Plan B (`kimi acp`) is fully deferred and clearly gated

`loop.py:84-87,100-101` raises `NotImplementedError` if `backend=="acp"`. There is no half-built ACP client. This is the correct disposition pending the stdout-buffering investigation in `kimi-runtime.md §App-server analog`.

Pre-merge action: ensure the `--backend acp` CLI flag and `CLAUDE_ANYTEAM_KIMI_BACKEND=acp` env are documented as **unsupported in v1** — not just rejected at runtime. Currently they pass `KIMI_BACKENDS = {"headless", "acp"}` validation in `config.py:22,68-69`, then fail at `loop.py:_backend_feature_test`. Better UX would be to drop `"acp"` from the validator until Plan B ships. Not a blocker.

### 6. Test coverage is targeted, not category-complete (Task #18 in flight)

The shipped Kimi tests are `tests/test_kimi_invocation_shape.py` plus the spawn-shim and installer extensions. The full Phase-4 test suite per `kimi-build-team-brief.md` is still in flight: invoke parser, MCP config writer, effort mapping, registration, loop session policy, plan approval, prompts, sign-in, plus the skills/plugin assertion extensions. Re-grade once Task #18 lands.

### 7. Dead constant in invoke.py

`invoke.py:29-36` defines `WRAPPER_TOOL_NAMES = frozenset({"send_message", "task_update", "task_create", "read_inbox", "task_list", "read_config"})` and never uses it. Either wire it through to a future `--protocol-only` filter (preferred — bundle with deviation #5 follow-up) or delete to avoid confusion.

## Still in flight

- **Task #17** — docs/skills/plugin/npm bootstrap. Items 12, 16, 19, 20 above will be re-graded after #17 lands.
- **Task #18** — full Kimi test suite. Item 21 above will be re-graded after #18 lands.
- **Task #9** — PR creation, awaiting #17 and #18.

This audit should be re-run and updated once #17 and #18 merge. The §"Per-point status" table marks every "In flight" item with the owning task so the refresh is mechanical.

## Conclusion

Plan A Kimi support is **structurally complete and empirically correct against the runtime findings**. 7 of 8 critical findings ship cleanly; finding #5 ships with an accepted prompt-discipline mitigation that needs a follow-up shared-module change. The PR is **not yet merge-ready** because the user-facing surface (Tasks #17 and #18) still describes Kimi as "next" while the code routes it as "today". Once those land — and after one more refresh of this audit — the branch should be ready to file as the third first-class adapter behind Codex and Gemini.

Plan B (`kimi acp`) is deferred behind a single empirical blocker (stdout buffering when stdin stays open) that has a tractable investigation path. The Phase-2 follow-up should reuse Gemini's `jsonrpc_stdio.py` framing — protocol versions match at 1.

## Follow-up issue

**Wrapper exposes 7 `mcp_anyteam_*` shadow tools beyond the 6 protocol tools. Kimi mitigates via prompt discipline. Follow-up: add `--protocol-only` flag to `wrapper_server.py` for Kimi adapter use.**

Decision (`team-lead`, 2026-04-25): ship v1 as option (a) — accept the deviation. Rationale recorded with the decision:

- The shadow tools work correctly via the wrapper if a model accidentally calls one. The failure mode is "model uses `mcp_anyteam_shell` instead of Kimi's native `Shell`" — degraded ergonomics, not a correctness bug.
- A `wrapper_server.py --protocol-only` flag is a shared-module change that affects the Codex and Gemini callers too. That generalization deserves its own PR with explicit cross-backend consensus, not a tail-end addition to the Kimi PR.
- Prompt discipline (Kimi prompts mention only the 6 bare protocol tools and steer the model to Kimi built-ins) plus the `WRAPPER_TOOL_NAMES` counter constant are sufficient v1 guards.

Action carried into the follow-up issue (to file after this PR merges):

- Add `--protocol-only` (or `--enabled-tools <NAMES>`) flag to `src/claude_anyteam/wrapper_server.py`.
- Wire `backends/kimi/invoke.py:write_mcp_config` to pass `--protocol-only` by default (using the existing `WRAPPER_TOOL_NAMES = frozenset({...})` set as the canonical six).
- Decide and document whether Codex and Gemini should also opt into `--protocol-only` once the flag exists, or stay on the full 13-tool surface for backward compatibility.
- Add wrapper-server tests asserting that `--protocol-only` actually narrows the exposed tool set, plus a Kimi adapter test asserting the flag is present in the MCP config it writes.
