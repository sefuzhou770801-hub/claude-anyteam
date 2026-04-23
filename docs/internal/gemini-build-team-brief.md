# Gemini adapter build — team brief

**Team:** `gemini-build`
**Branch:** `gemini-adapter` (single PR at end into `main`)
**All teammates:** `gpt-5.5` at `xhigh` reasoning effort
**Plan:** Plan A (headless `gemini -p ... --output-format stream-json`), approved 8/10 in `docs/gemini-adapter-feasibility.md`
**Scope:** v1 Gemini adapter with parity-equivalent task/prose/plan flows to the Codex adapter, followed by a live battle-test
**Out of scope for v1:** Plan B ACP transport, Plan C google-genai finisher, shared-loop extraction, mid-task steer

---

## Source docs — read these first

1. `docs/gemini-adapter-feasibility.md` — approver's verdict and the 6 critical corrections to Plan A. **This is authoritative.** If a plan detail in `docs/internal/gemini-plans.md` conflicts with the feasibility doc, the feasibility doc wins.
2. `docs/internal/gemini-plans.md` §"Plan A" — concrete file list, invocation contract, settings shape, test list.
3. `docs/internal/gemini-research-official.md` — authoritative CLI flag/config/MCP/auth research.
4. `docs/internal/gemini-research-reverse.md` — deep-dive on `stream-json` event shape, `--resume` behavior, OAuth vs API-key vs Vertex auth flow, and MCP settings pitfalls.

Codex-side references you will mirror:
- `src/claude_anyteam/codex.py` — subprocess runner template
- `src/claude_anyteam/loop.py` — control plane template
- `src/claude_anyteam/registration.py` — registration pattern to generalize
- `src/claude_anyteam/wrapper_server.py` — stays unchanged
- `src/claude_anyteam/schema_validation.py` — stays unchanged

---

## Roles

### `codex-lead` — coordinator + merge gate

- Reads this brief and the feasibility doc end-to-end before acting.
- Breaks Plan A's implementation into discrete tasks and assigns them to the right teammate.
- **Has merge authority:** the PR does not get merged until the lead signs off. Lead BLOCKS merge on any of:
  - tests failing
  - new test coverage below the feasibility doc's required test list
  - any of the 6 "critical corrections" in the feasibility doc not visibly addressed in code
  - registration advertises Codex branding from a Gemini adapter (must be fixed)
- Keeps a running status doc at `docs/internal/gemini-build-status.md` updated at least once per major milestone.
- Does NOT write implementation code. Reviews and delegates.

### `codex-researcher` — live research

- Owns ongoing research that implementers need answers to *during* the build, not before.
- Ready to answer: current Gemini CLI flag surface (`gemini --help`), stream-json event shape on the installed version, whether `--acp` vs `--experimental-acp` is current, how the signed-in OAuth session caches auth (for the Q1(c) startup probe), whether `mcp_anyteam_*` tool name prefixing is still the Gemini convention on the installed version.
- Writes findings to `docs/internal/gemini-live-research.md` as they're discovered.
- Runs `gemini --help`, `gemini --version`, and probes on the installed binary. These are the ground truth — not the docs.
- Does NOT write production adapter code. May write one-off probe scripts.

### `codex-shared` — shared-infra changes

- **Task 1:** Generalize `src/claude_anyteam/registration.py` so the member `model` and `prompt` labels come from a parameter/settings rather than hardcoded `codex-cli` / `codex exec` strings. Codex's current values must still be the default when no override is given — this is a pure refactor at the Codex call sites.
- **Task 2:** Create `src/claude_anyteam/backends/__init__.py` and `src/claude_anyteam/backends/gemini/__init__.py` skeletons.
- **Task 3:** Extend `src/claude_anyteam/spawn_shim.py` to route `gemini-*` names to a new `gemini-anyteam` binary (or to `claude-anyteam-gemini` — pick one and document). Pattern: `^gemini-`. Preserve existing `codex-*` routing exactly.
- **Task 4:** Add Gemini-specific env vars to `src/claude_anyteam/env.py`: `CLAUDE_ANYTEAM_GEMINI_BINARY` and any transport selector. Reuse existing `CLAUDE_ANYTEAM_MODEL` for `--model` — do NOT add a Gemini-specific model env.
- **Task 5:** Add the `gemini-anyteam` (or chosen name) entry to `pyproject.toml` `[project.scripts]`.

### `codex-gemini-invoke` — transport layer

Writes `src/claude_anyteam/backends/gemini/invoke_exec.py` with four exported functions:

1. `feature_test(binary)` — probes `--output-format stream-json` and `--resume` support on the installed binary. Returns a result struct with booleans; does not raise.
2. `build_gemini_settings_home(team, agent)` — creates `~/.claude/teams/<team>/state/<agent>/gemini/` and writes `settings.json` containing an `mcpServers.anyteam` entry. **Uses the real `HOME` value in the wrapper server's `env` field** (feasibility doc critical correction #2).
3. `build_wrapper_server_config(team, agent, real_home)` — emits the `mcpServers.anyteam` JSON block. Server alias is `anyteam` (no underscores — Gemini-specific). Wrapper identity passed via `args: ["--team", ..., "--name", ...]`, not via env. `trust: true`, `timeout: 600000`.
4. `run_exec(prompt, cwd, schema, gemini_binary, resume_session_id, model, effort, gemini_home)` — runs `gemini -p <prompt> --output-format stream-json [--resume <id>]`, parses JSONL event stream, tolerates unknown events and non-JSON lines, captures final assistant text, extracts session id from the `init` event, counts wrapper tool calls, returns a `CodexResult`. Exactly mirrors the shape of `src/claude_anyteam/codex.py::run`.

Also writes `src/claude_anyteam/backends/gemini/config.py` with a `GeminiSettings` frozen dataclass (NOT a subclass of the Codex `Settings`).

### `codex-gemini-loop` — control plane

1. Writes `src/claude_anyteam/backends/gemini/prompts.py` — task/prose/plan prompt builders. Tool names MUST be `mcp_anyteam_send_message`, `mcp_anyteam_task_update`, etc. — not bare names. Otherwise structurally mirrors `src/claude_anyteam/prompts.py`.
2. Writes `src/claude_anyteam/backends/gemini/loop.py` — a fork of `src/claude_anyteam/loop.py` with narrow substitutions:
   - `GeminiLoopState` (has `gemini_session_id`, not `codex_session_id` / `app_server_last_thread_id`)
   - `run()` calls Gemini `feature_test()` at startup
   - task execution uses Gemini exec transport for both first-run and resumed runs
   - prose handling uses Gemini exec + Gemini prompts
   - plan mode uses Gemini exec + schema validation (reuse `schema_validation.py` unchanged)
   - inbox drain, task claim, shutdown, idle notification, task-complete/blocked messaging are STRUCTURALLY IDENTICAL — do not refactor them.
3. Writes `src/claude_anyteam/backends/gemini/cli.py` — the `claude-anyteam-gemini` entry point. Mirrors the current `cli.py` flags for team/name/cwd/poll/color/plan-mode/model. Uses `feature_test` at startup and implements the Q1(c) auth probe: try whatever the installed binary's signed-in session provides first; if that fails and `GEMINI_API_KEY` is set, use it; if both fail, error out with a clear message naming both paths.

### `codex-tester` — test author

Writes **all** of these, does NOT co-author implementation code:

1. `tests/test_gemini_exec_invocation_shape.py` — argv + timeout + model/effort passthrough
2. `tests/test_gemini_stream_parse.py` — parses init/message/tool_use/tool_result/result/error events, tolerates malformed lines
3. `tests/test_gemini_settings_injection.py` — writes isolated `$HOME/.gemini/settings.json` with wrapper entry; real `HOME` survives in wrapper env
4. `tests/test_gemini_prompts.py` — tool names are `mcp_anyteam_*`, schema embedded inline
5. `tests/test_gemini_loop_unit.py` — loop state transitions with mocked invoke; session resume path
6. `tests/test_gemini_schema_retry.py` — invalid JSON first pass, stricter retry second pass, blocked on second failure
7. `tests/test_gemini_feature_test.py` — feature_test against mocked `gemini --help`
8. Extends `tests/test_spawn_shim.py` with `gemini-*` routing, preserving existing `codex-*` routing
9. Adds registration regression test covering backend-specific member metadata (no Codex branding bleed)

Target: 20+ new passing tests. Total project test count 202 → 222+.

### `codex-reviewer` — parity auditor

- Reads finished work against the 6 critical corrections in the feasibility doc and files issues (as team messages to the lead) for every gap found.
- Audits:
  - Is the wrapper `HOME` preserved correctly? (Critical correction #2)
  - Is `.gemini/settings.json` isolated under `~/.claude/teams/<team>/state/<agent>/gemini/` and NOT in the repo? (Critical correction #1)
  - Is `loop.py` unchanged? (Critical correction #3 — Gemini path forked, not threaded through)
  - Is `registration.py` generalized? (Critical correction #4)
  - Do Gemini prompts use `mcp_anyteam_*`? (Critical correction #5)
  - Is `CLAUDE_ANYTEAM_MODEL` reused? (Critical correction #6)
- Runs the full test suite (not just gemini tests) and reports Codex-path regressions.
- Signs off to the lead when ready for the live battle-test.

---

## Ground rules

1. **Commit style.** Small, focused commits on `gemini-adapter`. Commit messages use the `Co-Authored-By: Codex (gpt-5.5) <noreply@openai.com>` trailer. Co-authors for your commits are up to you — the lead's final merge commit credits the whole team.
2. **No force-push, no merge commits from main into the feature branch.** If you need to rebase, coordinate with the lead first.
3. **Do not modify anything under `src/claude_anyteam/` that Codex depends on without the lead's explicit OK.** Codex regressions are an automatic merge block. The `registration.py` refactor is the one exception — and it must preserve Codex's current behavior as the default.
4. **Do not modify `vendor/` or `hooks/` without explicit approval.**
5. **Do not delete or reshape tests.** Extend existing ones; add new ones.
6. **Always resolve the schemas by embedding them inline in the prompt** — Gemini has no `--output-schema` equivalent. See feasibility doc.
7. **When blocked, post to `#codex-lead` via `send_message` with a specific ask.** Do not sit idle; do not guess.
8. **Update `docs/internal/gemini-build-status.md` (lead) or `docs/internal/gemini-live-research.md` (researcher) at natural milestones.**

---

## Exit criteria (must all hold before the lead approves merge)

- `gemini-*` teammate spawned via shim routes correctly
- Adapter registers without Codex branding
- task, prose, plan flows all complete through Gemini exec against a real installed `gemini` binary
- Wrapper MCP tools (`send_message`, `task_update`, etc.) callable from Gemini as `mcp_anyteam_*`
- Schema validation + retry behavior match the current Codex guarantees
- All 6 critical corrections visibly addressed in code (reviewer confirms)
- 20+ new Gemini tests passing, zero Codex regressions
- Live battle-test: a team with `codex-*` (gpt-5.5 xhigh), `gemini-*` (via new adapter), and 1–2 Claude teammates (sonnet/haiku to keep cost down) completes a representative task end-to-end. Bugs found → filed → fixed; report written to `docs/internal/gemini-battle-test-report.md`.
- `docs/configuration.md` and `README.md` updated to reference the Gemini adapter
- `docs/roadmap.md` moves "Gemini" from "Coming next" to "Shipped"

When all exit criteria hold, the lead opens a PR from `gemini-adapter` into `main` with a summary and test-plan checklist, requests human review, and does NOT self-merge.
