# kimi-build team brief

**Workspace:** `/home/rosado/Projects/codex-teammate`
**Branch to create:** `feat/kimi-adapter` (off `main` @ 8da3e90)
**Team:** `kimi-build` — 1 Opus 4.7 lead (Claude, this session) + 1 Opus 4.7 architect (`kimi-architect`) + 4 Codex gpt-5.5 xhigh workers (`codex-runtime`, `codex-loop`, `codex-installer-docs`, `codex-tests`)
**Goal:** Ship a Kimi CLI backend at the same parity bar as the Gemini backend, in one mergeable PR series.

## Why Kimi (motivation, read this)

Source: `docs/internal/kimi-rationale.md` and `docs/internal/strategic-roadmap.md`.

Kimi CLI is the most architecturally distinct mainstream coding CLI on the market — first-class swarm primitives (300 sub-agents), ACP for IDE integration, native skills, MCP support. Adapting it forces the abstraction question: **what does it mean to wrap a CLI that already has its own multi-agent model?** Getting that right here makes GLM, DeepSeek, Qwen, and the generic API adapter trivially easy.

We earn the right to claim "any LLM" only when this lands. Don't optimize for ship speed at the cost of generality — the abstraction we land here will get re-used five times.

## Reference: how Gemini was delivered

Mirror this template. Read all three before writing code:

- `docs/internal/gemini-integration/codex-parity-map.md` — the 22-point checklist of every Codex touchpoint that needed a Gemini counterpart. Build the Kimi equivalent before implementing.
- `docs/internal/gemini-integration/gemini-runtime.md` — empirical runtime research (CLI flags, event shapes, MCP settings, resume semantics, ACP).
- `docs/internal/gemini-integration/repo-integration-parity-audit.md` — what was actually shipped vs. planned.
- Code: `src/claude_anyteam/backends/gemini/` — `cli.py`, `config.py`, `invoke.py`, `loop.py`, `prompts.py`, `acp.py`, `acp_client.py`, `crash_hygiene.py`.

## Probed Kimi CLI surface (verified `kimi --help`)

- **Binary:** `/home/rosado/.local/bin/kimi`. Already installed and signed in.
- **Headless:** `kimi --print/-p [PROMPT] --output-format stream-json --final-message-only --quiet`
- **Resume:** `--continue/-C` for last session in CWD; `--session/-S <id>` for explicit
- **Model:** `--model/-m <slug>`
- **Thinking:** `--thinking` / `--no-thinking` (binary switch, not graded)
- **MCP:** `--mcp-config-file <FILE>` (repeatable) or `--mcp-config <JSON>` (repeatable)
- **Skills:** `--skills-dir <DIR>` (repeatable)
- **Agent spec:** `--agent <default|okabe>` builtin or `--agent-file <FILE>` custom
- **Limits:** `--max-steps-per-turn`, `--max-retries-per-step`, `--max-ralph-iterations`
- **Workdir:** `--work-dir/-w <DIR>`, `--add-dir <DIR>`
- **Config:** `--config <toml/json>` or `--config-file <FILE>` (default `~/.kimi/config.toml`)
- **Subcommands:** `login`, `logout`, `term` (Toad TUI), `acp` (ACP server), `info` (version/protocol), `export`, `mcp`, `plugin`, `vis`, `web`
- **Docs:** https://moonshotai.github.io/kimi-cli/ ; LLM-friendly: https://moonshotai.github.io/kimi-cli/llms.txt

## Plan A vs Plan B

Default to **Plan A (headless `kimi --print --output-format stream-json`)** for v1 parity. Same shape as Gemini Plan A. Plan B (ACP via `kimi acp`) is a follow-up — record limitations, don't block on it.

## Scope (parity checklist mirrored from Gemini)

### Phase 0 — Research (kimi-architect, codex-runtime parallel)

- [ ] **kimi-architect:** write `docs/internal/kimi-integration/kimi-runtime.md` covering: exact headless argv shape, stream-json event types (assistant text, tool calls, tool results, errors, session id), MCP config schema (does it match Gemini's `mcpServers` block? Anthropic's? OpenAI tools?), `--continue/--session` reliability, isolated config root strategy (`KIMI_HOME`-equivalent or `--config-file` only), trust/approval flags (does Kimi need a `--yolo`-equivalent? auto-approve? dangerous mode?), final-text capture, error/timeout shapes. Empirical probes only — read `kimi info`, dry-run `kimi -p` against a tiny prompt, capture raw stream-json output. Cite line/output evidence in the doc.
- [ ] **kimi-architect:** write `docs/internal/kimi-integration/codex-parity-map.md` (Kimi version) — same 22-point structure as the Gemini map, but rewritten for Kimi specifics. Identify open questions for runtime probes.
- [ ] **codex-runtime (in parallel):** write `docs/internal/kimi-integration/kimi-skill-and-agent-research.md` — Kimi's native skills (`--skills-dir`) and `--agent`/`--agent-file` are unique. Decide: hide them entirely (adapter is opaque), expose them as configuration, or surface them as additional capability. Same for swarm primitives — does the adapter let Kimi fan out internally and treat the result as "one teammate," or do we forbid that and always run single-step? Recommend a v1 default with rationale.

### Phase 1 — Runtime module (codex-runtime)

- [ ] `src/claude_anyteam/backends/kimi/__init__.py` (empty file like gemini)
- [ ] `src/claude_anyteam/backends/kimi/config.py` — `KimiSettings` dataclass; `from_env()` mirroring `GeminiSettings`. Reuse `CLAUDE_ANYTEAM_TEAM/NAME/CWD/POLL/COLOR/MODEL`. Add Kimi-specific: `CLAUDE_ANYTEAM_KIMI_BINARY`, `CLAUDE_ANYTEAM_KIMI_HOME`, `CLAUDE_ANYTEAM_KIMI_BACKEND` (`headless`|`acp`, default `headless`), `CLAUDE_ANYTEAM_KIMI_THINKING` (on|off, default on), `CLAUDE_ANYTEAM_KIMI_MAX_STEPS`, etc. Do NOT reuse `CODEX_BINARY` or any Gemini env. Effort: Kimi has only `--thinking`/`--no-thinking` — no graded effort. Map Codex `effort` like so: `minimal`/`low` → `--no-thinking`, `medium`/`high`/`xhigh` → `--thinking` (default on). Document this lossy mapping.
- [ ] `src/claude_anyteam/backends/kimi/invoke.py` — `feature_test()` (kimi presence + headless flag check via `kimi info` and `kimi --help`); `default_kimi_home()`; `ensure_adapter_state()`/`read_adapter_state()` mirroring gemini's; isolated `KIMI_HOME` setup if needed (or `--config-file` redirect — pick whichever the runtime research recommends); MCP config writer that produces a Kimi-shaped MCP config pointing at `claude-anyteam-wrapper` with team/agent args, restoring real `HOME` via `env`. Use a short alias like `anyteam` (avoid underscore/dash normalization issues — research what Kimi does to MCP names). The wrapper-MCP `_probe_wrapper_mcp` should use `sys.executable`, NOT hardcoded `python`.
- [ ] `src/claude_anyteam/backends/kimi/invoke.py` — `run()` driving `kimi --print --output-format stream-json` with model/thinking/MCP/cwd flags, `stdin=DEVNULL`, parse stream-json events, capture session id, count wrapper tool calls, classify final text, handle timeout/nonzero/errors. Return existing `CodexResult` shape (rename/alias if cleaner). Add prompt-side schema validation since Kimi has no `--output-schema` (use `claude_anyteam.schema_validation.inline_schema_prompt_fragment` and Python jsonschema like Gemini does).
- [ ] `src/claude_anyteam/backends/kimi/prompts.py` — Kimi-tailored task/prose/plan prompts. Use `mcp_anyteam_send_message` style tool names per actual Kimi MCP tool naming (research-driven; mirror Gemini if Kimi normalizes the same way). Embed schemas always.

### Phase 2 — Control loop & CLI (codex-loop)

- [ ] `src/claude_anyteam/backends/kimi/loop.py` — copy `backends/gemini/loop.py` shape; substitute Kimi feature test, `kimi_session_id`, Kimi prompts, `Running kimi on task #...` active form. No App Server / `turn/steer` for v1 (document as limitation). Inbox poll, registration via shared `register()` with `BackendMetadata(model="kimi-cli", prompt="Kimi teammate adapter…")`.
- [ ] `src/claude_anyteam/backends/kimi/cli.py` — `kimi-anyteam` entry point. Mirror `gemini-anyteam`'s argparse surface. Args: `--team --name --cwd --poll-s --color --plan-mode --kimi-binary --model --effort --kimi-home --backend headless|acp --thinking on|off`.
- [ ] **Optional, only if cleanly factored:** `src/claude_anyteam/backends/kimi/acp.py` + `acp_client.py` for Plan B. Investigate whether Gemini's `jsonrpc_stdio.py` ACP client can be reused or whether Kimi's ACP semantics differ enough to need a separate client. **For v1 ship, document Plan B as deferred unless trivially derivable.**

### Phase 3 — Repo integration (codex-installer-docs)

- [ ] `src/claude_anyteam/spawn_shim.py` — add `DEFAULT_KIMI_MATCH = r"^kimi-"`, `_kimi_route()`, `KIMI_BINARY`, `KIMI_SHIM_MATCH_ENV`, `_AGENT_CONFIG_KEYS` continues to forward `model`+`effort`. Resolve `kimi-anyteam` adapter binary. Tests in `tests/test_spawn_shim.py`.
- [ ] `pyproject.toml` — add `kimi-anyteam = "claude_anyteam.backends.kimi.cli:main"` and `claude-anyteam-kimi = "..."`. Update description to mention Kimi.
- [ ] `src/claude_anyteam/installer.py` — add a `KimiCliCheck` analogous to `GeminiCliCheck`/`CodexCliCheck`: probe `kimi info`, parse version, warn on missing/old, document install + `kimi login` hint. Add install state keys `kimi_cli_found`, `kimi_cli_version`. Don't block Codex/Gemini users on Kimi missing.
- [ ] `src/claude_anyteam/registration.py` — should already accept `BackendMetadata`; verify Kimi flow registers cleanly with `model: "kimi-cli"`.
- [ ] `hooks/session-start.sh` — extend the orientation message to mention `kimi-*` routing alongside `codex-*` and `gemini-*`.
- [ ] `.claude-plugin/marketplace.json`, `.claude-plugin/plugin.json` — extend descriptions.
- [ ] `skills/help/SKILL.md` — teach Claude about `kimi-*` teammates: when to choose them (architectural-stretch tasks, large context, swarm workflows), regex `^kimi-`, the per-teammate config shape `{"model": "kimi-k2", "effort": "high"}` or whatever the actual Kimi model slug is (research → docs).
- [ ] `docs/architecture.md`, `docs/configuration.md`, `docs/install.md`, `docs/roadmap.md`, `README.md` — Kimi quickstart, prefix guidance, Kimi CLI prereq + auth, model catalog (research what Kimi exposes), TUI presence, mixed-team examples, known limitations vs Gemini/Codex.
- [ ] `npm/bin/setup.js`, `npm/lib/detect.js`, `npm/README.md` — extend success copy / detect to mention Kimi.

### Phase 4 — Tests (codex-tests)

Mirror Gemini test categories. Write before or alongside implementation, and run the full suite at the end. Each Codex worker is responsible for tests against their own module; codex-tests owns the cross-cutting suite.

- [ ] `tests/test_kimi_invocation_shape.py` — argv shape: `kimi --print --output-format stream-json --model X --thinking …`.
- [ ] `tests/test_kimi_invoke.py` — stream-json event parsing, session-id capture, tool-call counting against fixture transcripts.
- [ ] `tests/test_kimi_mcp_config.py` — MCP config writer, isolated HOME, real-HOME env restoration, alias normalization, wrapper command absolute path.
- [ ] `tests/test_kimi_effort.py` — thinking on/off mapping from Codex effort tiers.
- [ ] `tests/test_kimi_registration.py` — Kimi metadata in `~/.claude/teams/{team}/config.json`.
- [ ] `tests/test_kimi_loop_session_policy.py` — resume vs fresh dispatch, session-id reuse, recovery after invalid session.
- [ ] `tests/test_kimi_plan_approval.py` — plan-mode flow with schema-constrained plan output.
- [ ] `tests/test_spawn_shim.py` — extend with `^kimi-` route cases (no breakage to codex/gemini).
- [ ] `tests/test_skills.py`, `tests/test_plugin_bundle.py` — assert kimi appears in hook/plugin/skill text.
- [ ] `tests/test_install_command.py` — extend with Kimi prereq check (informational, non-blocking).
- [ ] **Run full pytest at end** — must pass with the existing 201 tests + new Kimi tests, with `pytest -m "not integration"` baseline. No regressions.

## Coordination rules

- **Read the codebase before judging.** Hard rule. Open the gemini files in full before starting your kimi counterpart. Don't propose fixes from memory — empirical only.
- **No rewrites of shared modules without consensus.** `protocol_io.py`, `messages.py`, `wrapper_server.py`, `schemas/`, `registration.py`, `schema_validation.py` are shared. If you need to generalize one, post the diff plan in your task and tag `@team-lead` (Claude) before merging.
- **One PR series, branch `feat/kimi-adapter`.** Phase 0 doc-only commits first, then Phase 1 runtime, then Phase 2 loop, then Phase 3 repo-integration, then Phase 4 tests. Each phase is a separate commit (or small commit cluster) on the branch.
- **Per-teammate configs already wrote**: gpt-5.5 + xhigh for all four Codex workers. You're already on max effort.
- **Stream of consciousness allowed in your turns** — but the *final artifact* (code, doc, test) must read like deliberate engineering, not exploration logs.
- **When you complete a task, mark it done in the task list and pick the next available one.** Prefer ID order. If blocked, message team-lead.
- **Mid-turn steering:** team-lead may send you redirection messages. Treat them as priority.

## Definition of done

- All 22 Gemini-parity items have a Kimi answer (implemented, deferred-with-doc, or N/A-with-rationale).
- New tests pass; existing 201 tests still pass.
- README + docs updated; help skill teaches Kimi.
- A new `docs/internal/kimi-integration/` directory mirrors `docs/internal/gemini-integration/` with at minimum: `kimi-runtime.md`, `codex-parity-map.md`, `repo-integration-parity-audit.md`.
- Branch `feat/kimi-adapter` ready for merge to main; PR description written.

## Out of scope for v1

- Kimi swarm/sub-agent fan-out as a first-class anyteam construct (Phase 2 design work; document the open question, don't ship a half answer).
- Hosted control plane / billing (strategic-roadmap.md Phase 3).
- Cross-host bindings (strategic-roadmap.md Phase 3).
