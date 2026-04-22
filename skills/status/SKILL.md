---
description: Show codex-teammate plugin setup status and the adapter launch command.
disable-model-invocation: true
---

Run a short Bash check against `~/.claude/settings.json` to determine whether
`env.CLAUDE_CODE_TEAMMATE_COMMAND` is present and non-empty.

Then reply with exactly two bullet points:
- `Setup:` say `configured` or `not configured`
- `Launch:` show `setsid nohup codex-teammate --team <team> --name codex-<name> --cwd <workspace> </dev/null >/tmp/codex-teammate.stdout 2>/tmp/codex-teammate.stderr & disown`

Keep the response short and do not add extra commentary.
