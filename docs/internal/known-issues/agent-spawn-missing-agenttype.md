# Known issue: Agent-tool spawn omits `agentType`, breaking MCP validation

**First observed:** Recurring across multiple sessions; documented 2026-04-25.
**Severity:** P1 product correctness — breaks inter-teammate communication on every freshly-spawned team until manually patched.
**Status:** documented, not yet fixed.

## Symptom

When a leader spawns a teammate via the host `Agent` tool with `team_name` + `name`, the resulting `~/.claude/teams/{team-name}/config.json` entry for that member omits the `agentType` field:

```json
{
  "agentId": "codex-alice@my-team",
  "name": "codex-alice",
  "color": "blue",
  "joinedAt": 1777076163651,
  "tmuxPaneId": "%0",
  ...
  // no agentType field
}
```

Only the `team-lead` member (set by `TeamCreate`) consistently has `agentType`.

When the spawned teammate boots and its MCP probe validates the team config, the probe rejects the config because `agentType` is required. Any `SendMessage` from that teammate then fails with `TeamConfig` validation errors. Inter-teammate communication is dead until the lead manually edits the config to add `agentType` for every affected member.

## Repro

1. From a leader Claude Code session: `TeamCreate(team_name="repro")`, then `Agent(team_name="repro", name="codex-test", prompt="...")`.
2. Read `~/.claude/teams/repro/config.json` and grep for `agentType` — only team-lead will have it.
3. Wait for the spawned teammate to try its first `SendMessage`. Observe the validation error.

## Workaround in use today

The leader patches the config immediately after every `Agent` spawn:

```python
import json
path = "~/.claude/teams/<team>/config.json"
data = json.load(open(path))
type_map = {"codex-alice": "researcher", ...}  # one entry per spawned member
for m in data["members"]:
    if m["name"] in type_map and "agentType" not in m:
        m["agentType"] = type_map[m["name"]]
json.dump(data, open(path, "w"), indent=2)
```

This works but is brittle: every time the runtime rewrites the config (it does this on every member-state change), there's a window where validation can fail again. In practice the patch sticks because the runtime preserves unknown fields, but if the runtime ever does a strict re-write, the patch would be lost.

## Proper fix candidates

Two viable paths:

1. **Spawn-side fix.** The host's `Agent` tool spawn shim populates `agentType` from the prompt or from a default ("teammate"). Whoever owns the host-side Agent shim — likely outside this repo — needs to add the field at spawn time.

2. **Validation-side fix in `claude-anyteam`.** The teammate-side MCP probe makes `agentType` optional, defaulting to a generic value when missing. This is fully within this repo's control and is the lower-risk fix.

Recommendation: do both. (1) is the right architectural fix; (2) is defensive insurance in case (1) ever regresses or another spawn mechanism shows up.

## Why this hasn't been fixed yet

- The workaround is well-understood and quick (a `python3 << 'EOF'` block patches all members in one shot).
- Memory note `feedback_team_config_agenttype.md` reminds future sessions to patch proactively.
- Each affected session has been time-boxed, so the workaround was acceptable.

But it's a real bug — a fresh user trying to spawn a team via the documented `Agent` flow would hit this immediately and have no obvious way to recover. Worth fixing alongside the next round of installer or wrapper-MCP work.
