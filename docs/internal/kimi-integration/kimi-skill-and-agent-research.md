# Kimi skills, agents, and swarm scope decision

**Owner:** codex-runtime
**Date:** 2026-04-25
**Status:** v1 recommendation for the Kimi adapter PR series

## Executive decision

For v1, treat a Kimi-backed teammate as **one anyteam teammate backed by one root Kimi session**. Do **not** surface Kimi skills, Kimi agent files, or Kimi subagents as first-class anyteam capabilities in the Claude leader UI/protocol yet. Preserve Kimi's native value by exposing skills and agent selection as **advanced configuration**, and allow Kimi's default internal subagent fan-out to run **inside** that single teammate boundary.

Default stance:

| Surface | v1 decision | Default | Escape hatch |
| --- | --- | --- | --- |
| Native skills / `--skills-dir` | Expose as configuration, not an anyteam capability | Do not pass `--skills-dir`; let Kimi perform its normal project/user/built-in discovery | Adapter config/env can add explicit skill dirs; prefer additive config over `--skills-dir` override |
| `--agent` / `--agent-file` | Expose as configuration, not an anyteam capability | Use Kimi built-in `default` agent | Config/env/per-agent file may choose `default`/`okabe` or a custom agent file |
| Built-in subagents / swarm | Hide as anyteam capability; allow opaque internal fan-out | Let the root Kimi `default` agent use its native `Agent` subagent tool when it decides it is useful | Config switch or custom agent file can disable the `Agent` tool for deterministic/single-agent runs |

This gives the adapter a narrow, shippable contract: Claude/anyteam delegates to `kimi-*`; Kimi may use its own tools and internal subagents to produce the answer; only the root Kimi teammate registers in team config, owns one inbox, claims one task, and emits one final completion message.

## Evidence gathered

Official Kimi docs and local CLI probes agree on the relevant surfaces:

- `kimi --help` on this machine reports **Kimi CLI 1.39.0** and confirms `--agent`, `--agent-file`, repeatable `--skills-dir`, `--print`, `--output-format stream-json`, `--mcp-config-file`, `--mcp-config`, and `--max-steps-per-turn` are available.
- `kimi info` reports `kimi-cli version: 1.39.0`, `agent spec versions: 1`, and `wire protocol: 1.9`.
- The command reference documents `--agent NAME` with built-ins `default` and `okabe`, `--agent-file PATH`, `--print`/`stream-json`, MCP config flags, yolo/plan/thinking flags, and repeatable `--skills-dir` ([Kimi command docs](https://moonshotai.github.io/kimi-cli/en/reference/kimi-command.html)).
- Print mode is the correct v1 integration surface because it is non-interactive, emits JSONL via `--output-format=stream-json`, and exits with documented retryable/non-retryable exit codes ([Print Mode](https://moonshotai.github.io/kimi-cli/en/customization/print-mode.html)).
- Skills are prompt/workflow extensions discovered at startup. Kimi injects skill names, paths, and descriptions into the system prompt and lets the model decide whether to read the `SKILL.md` file. Kimi auto-discovers project/user/generic/brand skill roots; `--skills-dir` is repeatable but overrides auto-discovered project/user roots; `extra_skill_dirs` is additive ([Agent Skills](https://moonshotai.github.io/kimi-cli/en/customization/skills.html)).
- Agent files are YAML specs with `extend`, `system_prompt_path`, `tools`, `exclude_tools`, and `subagents`; system prompts can see `${KIMI_SKILLS}` and other Kimi-provided context ([Agents and Subagents](https://moonshotai.github.io/kimi-cli/en/customization/agents.html)).
- Kimi's default agent includes built-in subagent types (`coder`, `explore`, `plan` in current docs) and an `Agent` tool that can start/resume isolated subagent instances, including background runs with timeouts. Current docs say subagents have isolated context, can run in parallel, and persist instance metadata under the session directory ([Agents and Subagents](https://moonshotai.github.io/kimi-cli/en/customization/agents.html)).

## Design boundary

The anyteam protocol already has an agent/team model:

- A spawned teammate has one `agent_name`, one inbox, one registration entry, one task claim, and one completion response.
- The Claude leader already decides when to spawn additional teammates and coordinates visible cross-teammate messaging.
- The adapter's job is to make the external backend act like a teammate, not to expose every internal primitive of that backend as a new anyteam primitive.

Kimi violates the simplifying assumption that a backend is single-agent internally. The clean v1 boundary is therefore:

> **Kimi subagents are private implementation details of the Kimi teammate, similar to shell commands, code search, or web fetches. They are not anyteam teammates unless a later design explicitly bridges Kimi subagent events into the anyteam protocol.**

That rule keeps v1 compatible with the existing Gemini/Codex control-loop shape and avoids inventing partial semantics for nested teammate identities.

## Surface-by-surface decision

### 1. Native skills

**Decision:** expose as adapter configuration; do not surface as an anyteam capability.

**Default:** do not pass `--skills-dir` in the default invocation.

Rationale:

- Kimi skills are already an open, cross-tool format and Kimi knows how to discover them. Reimplementing skill discovery in anyteam would duplicate vendor behavior and create version skew.
- `--skills-dir` is not additive according to docs: it overrides automatic project/user discovery. Passing a generated empty directory or always passing an explicit directory would accidentally hide `.kimi/skills`, `.claude/skills`, `.codex/skills`, `.agents/skills`, and user-level skills.
- Skills are prompt guidance, not protocol tools. The Claude leader should not be promised that it can enumerate, inspect, or invoke Kimi skills through anyteam in v1.
- Kimi's project-level discovery is useful for this adapter: if a repo intentionally ships Kimi/Codex/Claude-compatible skills, a Kimi teammate should benefit without extra anyteam plumbing.

Implementation guidance:

- Add an advanced setting such as `CLAUDE_ANYTEAM_KIMI_EXTRA_SKILLS_DIRS` or per-agent JSON key `kimi_extra_skills_dirs` as a path-list.
- Prefer writing these into adapter-owned Kimi config as `extra_skill_dirs` because the docs call that additive. Use repeatable `--skills-dir` only for an explicit override setting, e.g. `CLAUDE_ANYTEAM_KIMI_SKILLS_DIRS_OVERRIDE`, and document that it disables default discovery.
- Do not copy this process's Codex/Claude skill list into Kimi prompts. Let Kimi read its own discovered skill catalog via `${KIMI_SKILLS}`.
- Do not add any anyteam MCP tools like `list_kimi_skills` in v1. That would imply stable introspection and invocation semantics we do not need for parity.

### 2. Agent selection and custom agent files

**Decision:** expose as adapter configuration; do not surface as a runtime capability.

**Default:** use Kimi's built-in `default` agent.

Rationale:

- Agent choice changes Kimi's root system prompt, tool set, and subagent definitions. That is too large a semantic change to let the Claude leader toggle casually mid-task.
- Custom `--agent-file` is useful for expert users, test fixtures, and future single-agent/no-swarm mode, but it should be an explicit teammate configuration decision made before the Kimi process starts.
- `--agent` and `--agent-file` are mutually exclusive. Surfacing both as a high-level anyteam capability would require validation UI, file distribution, and trust rules that are out of scope for v1.
- Maintaining an adapter-owned full agent file as the default would create tool-list drift with Kimi releases. The built-in `default` agent is Kimi's compatibility contract and should remain the default.

Implementation guidance:

- Add config fields:
  - `CLAUDE_ANYTEAM_KIMI_AGENT=default|okabe`
  - `CLAUDE_ANYTEAM_KIMI_AGENT_FILE=/abs/path/to/agent.yaml`
  - per-agent JSON equivalents: `kimi_agent`, `kimi_agent_file`
- Validate mutual exclusion in `KimiSettings` and invocation tests.
- Pass `--agent <name>` only when the user configured a non-default built-in; otherwise omit it.
- Pass `--agent-file <path>` only when explicitly configured. Require an absolute path or resolve relative paths against the teammate cwd and record the resolved path in logs.
- Keep task prompts backend-owned: even when a custom agent file is used, the anyteam prompt still must instruct Kimi about required final JSON and `mcp_anyteam_*` protocol tools.

### 3. Built-in subagents / swarm primitives

**Decision:** do not expose Kimi subagents as anyteam teammates or explicit anyteam capabilities in v1. Let Kimi's root agent use internal subagents by default, and treat all such work as part of one teammate turn.

**Default:** Kimi internal fan-out is enabled because the built-in `default` agent has the `Agent` tool and built-in subagent types.

Rationale:

- It preserves Kimi's differentiating value. Disabling subagents by default would reduce Kimi to a less-tested single-agent mode and discard the reason this adapter is architecturally interesting.
- It preserves anyteam's external invariant. The leader asked one teammate (`kimi-*`) to work on one task; whether that teammate internally searches with one tool call or three subagents is an implementation detail.
- It avoids half-mapped identities. Kimi subagents do not have anyteam inboxes, task IDs, registration entries, colors, or TUI panes. Surfacing them as teammates without those properties would produce misleading presence and message semantics.
- It keeps v1 small. A real bridge would need session-event parsing, subagent lifecycle mapping, cancellation/timeout semantics, provenance in task completion messages, and probably UI changes.
- It is safer than trying to mirror Kimi's swarm. Letting Kimi own its internal scheduler avoids conflicts between Claude's spawn policy and Kimi's subagent policy.

Controls required for this default:

- Always run under adapter process timeouts and Kimi `--max-steps-per-turn`/config caps where available.
- In the root task prompt, tell Kimi: use internal subagents only for bounded, independent subwork; prefer foreground subagents; avoid background subagents unless their result is not needed for the final answer; do not ask the human user questions because this is a non-interactive teammate run.
- Count/log root stream-json tool calls. If Kimi emits `Agent` tool calls in the root JSONL stream, classify them separately in debug logs as internal fan-out evidence.
- Preserve one task claim and one completion message regardless of internal subagent count.

Single-agent opt-out:

- Add a config knob such as `CLAUDE_ANYTEAM_KIMI_SWARM=auto|off` (default `auto`).
- `auto`: normal default agent; Kimi may use `Agent`.
- `off`: adapter passes an explicit custom agent file that extends `default` and excludes `kimi_cli.tools.agent:Agent` (and, defensively, any dynamic-subagent tool if enabled in the selected Kimi version). This is for cost-sensitive, deterministic, or audit-sensitive runs.
- Do not make `off` the default unless empirical runtime probes show the Kimi `Agent` tool breaks print-mode JSONL parsing, wrapper MCP access, process timeouts, or final-message capture.

## Alternatives considered

### A. Hide all Kimi-native surfaces entirely

This would mean always invoking Kimi with default discovery/agent and offering no skill/agent/swarm knobs.

Pros:

- Smallest implementation.
- Fewer user-facing support cases.

Cons:

- Users cannot point Kimi at team skills or custom agent files without modifying global Kimi config.
- Tests cannot easily force a no-swarm or custom-tool policy.
- It underuses Kimi's documented extension model.

Verdict: too restrictive. Use config exposure instead.

### B. Surface skills/agents/subagents as anyteam capabilities

This would mean Claude can list Kimi skills, invoke Kimi skills directly, choose Kimi agents per task, or spawn Kimi subagents as visible team members.

Pros:

- Richer orchestration and observability.
- Long-term path to heterogeneous nested swarms.

Cons:

- Requires new protocol semantics and UI expectations.
- Blurs authority: both Claude and Kimi would think they own agent spawning.
- High risk of false visibility: Kimi subagents would appear teammate-like but lack inboxes, approval state, and independent lifecycle in anyteam.
- Not needed for v1 parity with Codex/Gemini teammate behavior.

Verdict: defer to a dedicated Phase 2 design.

### C. Force single-step/single-agent Kimi by default

This would use an adapter-owned agent file excluding `Agent`.

Pros:

- More deterministic cost, timing, and event parsing.
- Easier mental model: one anyteam teammate equals one Kimi root model only.

Cons:

- Discards Kimi's core differentiation and likely reduces quality on codebase exploration tasks.
- Requires maintaining an agent-file delta against Kimi's built-in default tools.
- Makes Kimi less representative as the adapter that tests nested-agent boundaries.

Verdict: keep as opt-out, not default.

## Recommended v1 implementation checklist

1. `KimiSettings` fields:
   - `kimi_binary`
   - `model`
   - `thinking` / Codex-effort-to-`--thinking` mapping as already scoped in the team brief
   - `kimi_home` / config-file isolation as decided by runtime research
   - `agent` (`default|okabe|None`)
   - `agent_file` (`Path|None`)
   - `extra_skills_dirs` (`list[Path]`)
   - `skills_dirs_override` (`list[Path]`, optional/advanced)
   - `swarm` (`auto|off`, default `auto`)
2. Spawn shim may forward only stable, backend-owned keys initially: `model`, `effort`, and optionally Kimi-specific keys if the shim already branches by route. If forwarding arbitrary Kimi-specific per-agent keys makes the shim too broad, keep them env/CLI-only for v1 and document the limitation.
3. Invocation argv defaults:
   ```text
   kimi --print --output-format stream-json --work-dir <cwd> ...
   ```
   Add `--agent` / `--agent-file` / `--skills-dir` only when explicitly configured.
4. For additive skills, prefer adapter-owned config:
   ```toml
   extra_skill_dirs = ["/abs/path/team-skills", ".agents/skills"]
   ```
   Do not use `--skills-dir` for this common case because it overrides auto-discovery.
5. If `swarm=off`, generate an adapter-owned agent file in the Kimi state root and pass it via `--agent-file`. Keep the file tiny: `extend: default`, `exclude_tools: ["kimi_cli.tools.agent:Agent"]`, no custom prompt unless absolutely necessary.
6. Prompt Kimi that internal subagents are allowed but private: they must not send anyteam messages, claim tasks, or represent themselves as separate teammates.
7. Documentation should say: **`kimi-*` is a single visible teammate; Kimi may internally use subagents. Use `CLAUDE_ANYTEAM_KIMI_SWARM=off` for strict single-agent behavior.**

## Follow-up / Phase 2 questions

These are intentionally out of v1 scope:

- Can Kimi wire/ACP event streams expose enough subagent lifecycle data to show nested progress in Claude's TUI without registering fake teammates?
- Should anyteam have a generic `internal_parallelism` or `delegation_model` capability for backends like Kimi that can fan out internally?
- Can Kimi subagents safely use the anyteam MCP wrapper, and if so, should their tool calls be attributed to the root teammate or a synthetic subagent identity?
- Is there a useful bridge from anyteam skills to Kimi skills (for example, packaging team skills under `.agents/skills`) that can remain vendor-neutral?
- Should `kimi vis`/`kimi export` artifacts be linked from task completion messages for audit-heavy runs?


## Added fixture-bundle dependency

Team lead added a Phase 0/Phase 1 fixture-capture dependency after this decision doc was drafted: deterministic raw Kimi `stream-json` transcripts should eventually land under `tests/fixtures/kimi/` for parser and session-policy tests. As of this doc update, `tests/fixtures/kimi/README.md` is not present in this worktree, and codex-tests explicitly asked us to hold off until that redaction/normalization contract is available locally. Therefore this task does **not** generate fixture JSONL files yet. When the contract lands, capture the required transcripts (`simple_assistant_text`, `tool_call_lifecycle`, `multi_chunk_text`, `plan_mode_constrained`, `resume_session`, `fresh_session`, `invalid_session_recovery`, and `error_or_warning`) without editing except for contract-approved absolute-path redaction.

Separately, the installer sign-in detector should depend on kimi-architect's runtime answer for which file(s) `kimi login` writes under `~/.kimi`; this doc intentionally does not guess that auth-storage contract.

## Final recommendation

Ship v1 with **configured extensibility, opaque internal fan-out**:

- Skills: native Kimi discovery by default; additive/override directories configurable; no anyteam skill capability.
- Agents: built-in `default` by default; built-in/custom agent selectable before launch; no runtime agent switching capability.
- Swarm: Kimi may use internal subagents by default, but anyteam sees exactly one `kimi-*` teammate. Add `swarm=off` only as a deterministic opt-out.

This default maximizes Kimi's value while keeping the anyteam protocol honest: one visible teammate boundary, one task lifecycle, no invented nested-team semantics until we can design and test them deliberately.
