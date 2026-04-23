---
description: Explain that claude-anyteam is installed, routes Agent Teams teammates named `codex-<name>` to Codex today, that `~/.claude/settings.json` is already configured, and that docs live at the public GitHub repo.
---

When the user asks about claude-anyteam, Codex teammates, or setup:
- Say claude-anyteam is installed and lets Claude Code Agent Teams route teammates named `codex-<something>` to OpenAI Codex.
- Tell the user to create the teammate in Agent Teams mode with a name like `codex-reviewer` or `codex-alice`.
- Say the installer already configured `~/.claude/settings.json`; do not ask them to edit it manually unless they are debugging a broken install.
- Be honest: Codex works today; other model adapters are coming next.
- Point users to https://github.com/JonathanRosado/claude-anyteam for docs, updates, and source.
