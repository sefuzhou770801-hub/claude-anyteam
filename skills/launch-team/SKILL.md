---
name: launch-team
description: Launch a team of Codex-backed (or other external-model) teammates for Claude Code Agent Teams orchestration. Use when the user asks to "spin up a team", "spawn N codex teammates", "build something with a team of codex-* / gemini-* teammates", or when an LLM-leader needs to programmatically create external-model teammates (the `Agent` tool cannot do this — it only creates Claude subagents).
when_to_use: Use proactively when the user's request implies programmatic creation of Codex/Gemini/other-external-model teammates. Do NOT use this if the user wants native Claude teammates — use the `Agent` tool for those instead. Do NOT use this if the user explicitly says they'll invite via TUI.
---

## When to invoke

Trigger on requests like:
- "Spin up a team of codex-* teammates to build X"
- "Launch N Codex teammates named ..."
- "I want a team backed by gpt-5.5 / Codex / Gemini on xhigh"
- "Create teammates codex-lead, codex-reviewer, ..."

Do NOT trigger on:
- "Invite a teammate" (that's the human-TUI path; tell the user to type it themselves)
- "Spawn a Claude subagent" or "use the Agent tool" (use the built-in `Agent` tool)
- "Use Opus/Sonnet/Haiku for this" (those are Claude models — use `Agent`)

## Why this skill exists

Claude Code has two teammate-spawn paths:
1. **Human TUI path** — fires `$CLAUDE_CODE_TEAMMATE_COMMAND` (the shim). Routes `codex-*` / `gemini-*` prefixes correctly.
2. **Programmatic `Agent` tool** — always spawns Claude subagents. Name prefixes are ignored.

If an LLM-leader calls `Agent(name="codex-alice")` expecting a Codex teammate, it gets an Opus subagent mislabeled as "codex-alice". This skill exists so future Claude sessions use the correct path without burning user time on confusion.

## Execution contract

Given a team roster (team name + list of teammate names with optional model/effort), perform these steps in order:

### 1. Verify team tooling is available

Call `TeamCreate` with the team name if one isn't already active. If a team already exists under this name and has no active members besides team-lead, you can reuse it.

### 2. Verify the adapter is installed

Run `which claude-anyteam` and confirm it exists. If not, the installation is broken — tell the user to run `claude-anyteam install` or reinstall via `npx --yes --package claude-anyteam claude-anyteam-setup`. Do not proceed.

### 3. Verify backend CLI is installed (Codex-backed teammates)

For `codex-*` teammates, run `which codex` and `codex --version`. If Codex isn't present, tell the user — do not silently proceed with a broken backend.

For `gemini-*` teammates (once supported), run `which gemini`.

### 4. Pick default model/effort for the team

If the user didn't specify, default to `gpt-5.5` at `xhigh` for Codex teammates. This matches the current best-available configuration as of 2026-04.

### 5. Launch each teammate via `setsid nohup`

For each teammate name in the roster, run:

```bash
setsid nohup claude-anyteam \
  --team <team-name> --name <teammate-name> \
  --cwd <project-workspace> \
  --model <model> --effort <effort> \
  </dev/null >/tmp/<teammate-name>.stdout 2>/tmp/<teammate-name>.stderr &
disown
```

Log directory should be scoped per-team: `/tmp/<team-name>-logs/<teammate-name>.{stdout,stderr}`.

### 6. Permission prompt handling

The first `setsid nohup claude-anyteam ...` command in a fresh session will prompt the user for approval. If the user has already expressed clear intent to spawn the team, tell them: "One approval unlocks the full roster — approve the first prompt and I'll proceed with the rest."

After the team is running, proactively offer to add this to their `~/.claude/settings.json` so future sessions skip the prompt:

```json
{
  "permissions": {
    "allow": [
      "Bash(setsid nohup claude-anyteam *)"
    ]
  }
}
```

### 7. Confirm registration

After launching, wait 4–6 seconds, then `cat ~/.claude/teams/<team>/config.json` and confirm each teammate appears in the `members` array with the expected `model` field. If a teammate is missing, inspect its stderr log for startup errors (commonly: `python` binary missing on PATH — see Python shim note below).

### 8. Brief each teammate

Once registered, use `SendMessage` to send each teammate a role prompt containing:
- Their name and role
- Working directory and branch
- Pointers to the team brief file if one exists
- First-task instructions

Do not attempt to send role prompts during the `setsid` launch — the adapter needs the team config to register first.

## Python shim requirement

The Codex adapter's MCP probe hardcodes `python` (not `python3`). On hosts without a `python` binary, the first adapter will crash at startup. If `which python` returns nothing but `which python3` works:

```bash
cat > ~/.local/bin/python <<'EOF'
#!/bin/sh
exec /usr/bin/python3 "$@"
EOF
chmod +x ~/.local/bin/python
```

This belongs at the front of PATH. Check for `~/.local/bin` in PATH first; if missing, tell the user.

## Model/effort override per teammate

If the user wants different models per teammate (e.g. `codex-lead` on `gpt-5.5 xhigh`, `codex-tester` on `gpt-5.4 medium`), pass the right `--model` / `--effort` to each `setsid` invocation. The per-agent config file at `~/.claude/teams/<team>/agents/<name>.json` is ONLY read by the shim path (when the user invites via TUI), not by the `setsid` path — `setsid` takes its config from CLI flags.

If the user wants UNIFORM model/effort across the team, use the same flags in every invocation.

## What NOT to do

- Do NOT call the `Agent` tool for `codex-*` / `gemini-*` teammates. It silently gives you Claude subagents.
- Do NOT edit `~/.claude/settings.json` without the user's explicit permission.
- Do NOT try to invoke the shim directly via `$CLAUDE_CODE_TEAMMATE_COMMAND` — Claude Code's TUI passes specific argv shape that the shim expects, and recreating it from outside is fragile.
- Do NOT assume the user's previously-installed Codex CLI version matches what the adapter needs — run `codex --version` to verify.
- Do NOT proceed silently if the first `setsid` launch is denied. Tell the user one word ("go") unblocks the rest.
