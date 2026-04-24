# Install

The quickstart in the [README](../README.md#quickstart) is the normal path:

```bash
npx --yes claude-anyteam
```

That's the entire install. The installer:

- Detects `python3` and installs `uv` if missing (non-interactive, no shell profile edits)
- Installs the `claude-anyteam` Python tool via `uv tool install`
- Verifies a terminal multiplexer (tmux or psmux) is on PATH — see [configuration.md](configuration.md#teammate-display-mode) for install commands
- Writes the Claude Code hook (`CLAUDE_CODE_TEAMMATE_COMMAND`) and binary path to `~/.claude/settings.json`
- Sets `teammateMode="tmux"` in `~/.claude.json` (prompts before overwriting a non-default value)

## How installation works

One command writes two env vars to `~/.claude/settings.json`:

```json
{
  "env": {
    "CLAUDE_CODE_TEAMMATE_COMMAND": "/absolute/path/to/claude-anyteam-spawn-shim",
    "CLAUDE_ANYTEAM_BINARY": "/absolute/path/to/claude-anyteam"
  }
}
```

When Agent Teams mode spawns a teammate, Claude Code invokes `$CLAUDE_CODE_TEAMMATE_COMMAND` (the shim) instead of native `claude`. The shim inspects the agent name:

- `codex-*` → dispatches to the `claude-anyteam` adapter (Codex)
- Anything else → forwards to native `claude` (unchanged)

Every step is reversible. `claude-anyteam uninstall` cleanly removes the env vars and reverts `teammateMode` to whatever was there before (or removes it entirely if the install added it).

## Alternative install methods

```bash
# via uv directly (if you already have uv + Python)
uv tool install claude-anyteam && claude-anyteam install

# via Claude Code plugin (self-heals settings on every session)
claude plugin marketplace add JonathanRosado/claude-anyteam
claude plugin install claude-anyteam@claude-anyteam
```

All paths write the same settings. Pick whichever fits your workflow.

Installing the plugin also gives Claude a plugin-provided `/claude-anyteam:help` skill, so the assistant can explain that `codex-<name>` teammates route to Codex, that `~/.claude/settings.json` is already wired by the installer, and that the GitHub repo is the source of truth.

## Headless / persistent teammates

For headless and persistent background adapters (run across multiple Claude Code sessions):

```bash
setsid nohup claude-anyteam \
  --team my-team --name codex-alice \
  --cwd /path/to/workspace \
  --model gpt-5.5 --effort high \
  </dev/null >/tmp/codex-alice.stdout 2>/tmp/codex-alice.stderr & disown
```

This mode is fully messageable (inbox, task claim, peer replies) but does NOT render in Claude Code's TUI presence line — TUI visibility requires the leader-spawn path via the shim. Useful when you want the adapter running continuously regardless of the Claude Code session lifecycle.
