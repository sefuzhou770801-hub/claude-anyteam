# CLAUDE.md — project context for Claude Code

This repository is **claude-anyteam**: a plugin that lets Claude Code's Agent Teams feature route teammates named `codex-*` (soon `gemini-*`, `kimi-*`, etc.) to external model CLIs instead of native Claude.

## Critical guidance for orchestrating teammates

Claude Code has two distinct teammate-spawn paths. If you are an LLM assistant working in this repo and the user asks you to "spin up a team" or "launch N codex teammates," **you must pick the correct path or you will silently produce wrong-backend teammates**.

### Path 1: Agent tool (DO NOT USE for external-model teammates)

`Agent(name="codex-alice", team_name=..., model="opus")` spawns a native Claude subagent labeled "codex-alice." The name prefix is cosmetic. The `$CLAUDE_CODE_TEAMMATE_COMMAND` shim is NOT invoked. The teammate runs on whatever Claude model you specified — never Codex.

Use `Agent` ONLY when you want a Claude subagent (native `Agent` tool teammates). Never for `codex-*` / `gemini-*`.

### Path 2: setsid nohup (USE THIS for external-model teammates)

```bash
setsid nohup claude-anyteam \
  --team <team> --name codex-<role> \
  --cwd <project> \
  --model gpt-5.5 --effort xhigh \
  </dev/null >/tmp/<name>.stdout 2>/tmp/<name>.stderr &
disown
```

The adapter self-registers into the team, becomes messageable via `SendMessage`, and runs on the Codex/Gemini/etc. backend you specify. This path is documented in `README.md:136-148` and packaged as the `/claude-anyteam:launch-team` skill.

### First-launch permission prompt

The first `setsid nohup claude-anyteam ...` will trigger a permission prompt. Tell the user one word ("go") unblocks the rest. After launch succeeds, offer to add `Bash(setsid nohup claude-anyteam *)` to `~/.claude/settings.json` permissions so future sessions don't prompt.

### When the user wants a team with mixed backends

`codex-*` teammates: `setsid nohup claude-anyteam ...`
Native Claude teammates: `Agent(subagent_type="general-purpose", team_name=..., name="claude-...")`

Both can coexist in the same team; they register into the same `members.json`.

## Don't do these

- Don't try to hand-craft the shim's argv from outside Claude Code — the TUI passes a specific shape, and recreating it is fragile.
- Don't edit `~/.claude/settings.json` without explicit user permission.
- Don't invoke the `Agent` tool for `codex-*` / `gemini-*` names and assume it'll route — it won't.
- Don't spawn teammates immediately after the user said "tear them down" or similar cancel signals. Wait for a clear new authorization.

## Test + build conventions

- Python 3.12+, uv-managed.
- `.venv/bin/python -m pytest -q` runs the suite (~208 passing at main tip).
- Codex regressions block any Gemini/external-backend work. Run the full suite after touching shared modules.
- Branch `gemini-adapter` is the current external-backend feature branch. Keep `main` green.

## Docs index

- `README.md` — user-facing install + quickstart
- `docs/architecture.md` — how the adapter integrates with Claude Code's Agent Teams
- `docs/configuration.md` — CLI flags, env vars, per-teammate config
- `docs/roadmap.md` — shipped vs coming-next
- `docs/internal/` — WIP/feasibility/research (gemini-adapter-feasibility.md, gemini-plans.md, gemini-live-research.md, gemini-build-team-brief.md)
