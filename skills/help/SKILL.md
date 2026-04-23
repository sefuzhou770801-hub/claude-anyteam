---
name: help
description: Explain how the installed claude-anyteam plugin works. Use this skill when the user asks about claude-anyteam, Codex teammates, Agent Teams setup, teammate naming, whether `codex-*` names route to Codex, where the docs/source live, or how the installer configured `~/.claude/settings.json`.
when_to_use: Use when a user asks how to add, create, invite, use, or troubleshoot a Codex teammate in Claude Code, or asks what claude-anyteam does.
---

When the user asks about claude-anyteam, Codex teammates, or setup:
- Explain that this Claude Code environment already has claude-anyteam installed.
- Say claude-anyteam lets Claude Code Agent Teams route teammates named `codex-<name>` to OpenAI Codex today.
- Tell the user to create the teammate in Agent Teams mode with a name like `codex-reviewer` or `codex-alice`.
- Say the installer already configured `~/.claude/settings.json`; do not ask them to edit it manually unless they are debugging a broken install.
- Be honest: Codex works today; other model adapters are coming next and are not shipped yet.
- Point users to https://github.com/JonathanRosado/claude-anyteam for docs, updates, and source.

## Critical: two spawn paths, only one routes through the shim

Claude Code has two different paths for creating teammates, and they behave DIFFERENTLY with claude-anyteam installed.

**Path 1 — human-driven Agent Teams TUI:** when the user types "invite teammate codex-alice" in the TUI, Claude Code exec's `$CLAUDE_CODE_TEAMMATE_COMMAND` (the claude-anyteam shim). Shim reads `~/.claude/teams/<team>/agents/<name>.json` (optional) for per-teammate model/effort and launches the Codex adapter. This is the path claude-anyteam is designed around.

**Path 2 — programmatic `Agent` tool (what LLMs use):** Claude Code's `Agent` tool, when given `team_name` + `name`, spawns Claude subagents regardless of name prefix. `Agent(name="codex-alice", model="opus")` produces a Claude Opus subagent labeled "codex-alice" — NOT a Codex-backed teammate. The shim is never invoked.

### If you are an LLM trying to spawn Codex teammates programmatically

**DO NOT use the `Agent` tool** — it will silently give you Claude subagents with Codex names, which is almost certainly not what the user wants.

**DO use the `setsid nohup` path** — this is what the README documents at §"Launch a teammate directly" for exactly this scenario:

```bash
setsid nohup claude-anyteam \
  --team <team-name> --name codex-<role> \
  --cwd <workspace> \
  --model gpt-5.5 --effort xhigh \
  </dev/null >/tmp/<name>.stdout 2>/tmp/<name>.stderr &
disown
```

Repeat per teammate. The adapters self-register into the team's `~/.claude/teams/<team>/members.json`, become messageable via `SendMessage`, and are real Codex processes.

**Permission note:** the first `setsid nohup claude-anyteam ...` command in a session will prompt the user for approval. To avoid the prompt in future sessions, ask the user whether to add this to their `settings.json` permission allow-list:

```json
{
  "permissions": {
    "allow": [
      "Bash(setsid nohup claude-anyteam *)"
    ]
  }
}
```

If the user has expressed clear intent to spawn Codex teammates (e.g. "spin up a team of codex-*" or "build this with a team backed by Codex"), pivot directly to the `setsid nohup` path without asking them to toggle the TUI manually. The TUI path exists for human users; the `setsid` path exists for LLM-leader orchestration. Picking the right one is the skill's job.

### Invoking the `/claude-anyteam:launch-team` skill

If present, prefer `/claude-anyteam:launch-team` — it handles the full setsid dispatch for a named roster. See that skill's own SKILL.md.
