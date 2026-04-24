---
name: help
description: Spawn Codex-backed and Claude-backed teammates in Claude Code Agent Teams without user coaching. Use when a user wants to build a team with specific model backings per teammate (notably "backed by Codex/external model"), launches a mixed-model team, asks how to invite a Codex teammate, or asks how claude-anyteam works. Do NOT use for pure-Claude teams with no backend preference — Anthropic's native Agent Teams guidance covers that.
when_to_use: Use when the user expresses intent to build, launch, spawn, create, or invite a team/teammate AND any of the following hold — (a) they mention a specific model backing (Codex/Claude/Anthropic/OpenAI/GPT/external model), (b) they describe a mixed-model team, (c) they ask to add a `codex-*` teammate by name, or (d) they ask how claude-anyteam works. Do NOT use when the user only mentions a model outside a team-building context (e.g. "what is Codex?" is not a trigger).
---

## How to spawn a mixed-model team

When the user asks for a team with specific model backings, immediately call `TeamCreate` then `Agent(...)` — don't explain the mechanism, don't verify the install, don't ask follow-up questions about naming, team name, or prompt content when you can reasonably infer them from context.

The naming convention is how routing works: teammate names matching `^codex-` (regex) go to the OpenAI Codex adapter via `claude-anyteam`. Any other name goes to native Claude. The user doesn't have to know this; **you** pick names that honor their intent.

### What to infer vs what to ask

- **team_name:** infer if not given. Short, topical, hyphenated (`research-pod`, `build-team`, `review-squad`). Don't ask.
- **agent names:** infer from roles the user mentioned. Two researchers → `codex-researcher-1` / `codex-researcher-2` (or `codex-alice` / `codex-bob` if you prefer names). An implementer → `implementer` or `claude-implementer`. Don't ask.
- **prompt content:** if the user gave a concrete topic or task, put that in the prompt. If the user was generic ("two researchers," no topic), write a brief, role-appropriate prompt that describes the teammate's responsibilities on the team and invites them to wait for the first real task. Don't ask for a topic unless the user gave obvious signal they want one (e.g. they're mid-project and the topic is load-bearing).
- **When to ASK anyway:** if the user's message is ambiguous about WHICH model belongs where ("a mix of Codex and Claude" without specifying which role gets which), one clarifying question is appropriate.

### Example recipe — "two researchers backed by Codex and one implementer backed by Claude"

```
TeamCreate(team_name="research-pod")
Agent(team_name="research-pod", name="codex-researcher-1", prompt="<research brief>")
Agent(team_name="research-pod", name="codex-researcher-2", prompt="<research brief>")
Agent(team_name="research-pod", name="implementer", prompt="<implementation brief>")
```

`codex-researcher-1` and `codex-researcher-2` route to Codex; `implementer` stays on native Claude. The installed shim handles the routing; you don't need to configure anything.

### Tool-call parameters (exact)

The Agent tool's teammate-spawn signature takes three named parameters: `team_name`, `name`, `prompt`. `TeamCreate` takes `team_name`. If Claude Code's current session exposes a different parameter shape in its tool schema, cross-reference that schema — don't guess variants like `team=` or `agent_name=`.

### Naming rules

- Codex teammate → name starts with `codex-` (e.g. `codex-alice`, `codex-reviewer`, `codex-researcher-1`).
- Claude teammate → any name that does not match `^codex-` (e.g. `alice`, `implementer`, `reviewer`).
- If the user says "Codex" specifically, use `codex-*` naming. For other external-model requests ("OpenAI API directly", "GPT via the API", "o1", "Gemini", "Kimi", etc.), tell the user that claude-anyteam currently only supports Codex-backed teammates; other adapters are planned but not shipped. Do not silently route those names via `codex-*` — that would invoke Codex under a different label.

### Trust the session-start banner

This environment's session-start hook prints an orientation line confirming claude-anyteam is installed and wired. Do NOT re-verify `~/.claude/settings.json`, ask about install status, or suggest re-running `claude-anyteam install`. If the banner has scrolled off-screen, don't ask the user to scroll up — just try the tool calls. They will fail loudly with a real install error if something is actually wrong.

### When NOT to use this skill

- Pure-Claude teams with no mention of Codex or an external model: Anthropic's native Agent Teams guidance already covers this.
- Questions about `/agents` or `subagent_type=...` — that is the subagent tool, a different mechanism from teammate spawning.
- "What is Codex?" or other off-topic model questions — not a team-building trigger.
