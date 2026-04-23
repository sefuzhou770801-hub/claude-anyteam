# Known issue: leader cannot address externally-spawned adapters via SendMessage

**First observed:** 2026-04-23 during the vendoring task (this session).
**Severity:** P1 product correctness — undermines a core promise of the adapter.
**Status:** documented, not yet fixed.

## Symptom

A Claude Code leader (the `team-lead` running in your interactive Claude Code session) cannot address externally-launched `claude-anyteam` adapters with `SendMessage`. Both forms fail:

```
SendMessage(to="codex-vendor")
  → "No agent named 'codex-vendor' is currently addressable. Spawn a new one or use the agent ID."

SendMessage(to="codex-vendor@friction-research")
  → "to must be a bare teammate name — there is only one team per session"
```

Even though:
- The adapter process is alive (`ps aux` shows it)
- The adapter is registered in `~/.claude/teams/{team}/config.json` (verified by reading the file)
- The adapter's inbox file exists at `~/.claude/teams/{team}/inboxes/{name}.json`
- The adapter is polling at `poll_s` interval (verified by stderr log)
- **Peer adapters CAN message each other** (verified during multi-codex sessions earlier in the same workflow)

## Reproducer

In an interactive Claude Code session (the leader is whatever Claude session you're typing into):

```bash
# 1. Bootstrap a team config (or use an existing one this session didn't witness being created)
python3 -c "import json,time; from pathlib import Path; \
  cfg={'name':'demo','leadAgentId':'team-lead@demo','leadSessionId':'x','createdAt':int(time.time()*1000),'members':[{'agentId':'team-lead@demo','name':'team-lead','agentType':'team-lead','model':'claude-sonnet-4-6','joinedAt':int(time.time()*1000),'tmuxPaneId':'','cwd':'/tmp','subscriptions':[],'backendType':'in-process'}]}; \
  p=Path.home()/'.claude/teams/demo/config.json'; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(cfg,indent=2))"

# 2. Launch an adapter externally
setsid nohup claude-anyteam --team demo --name codex-probe --cwd /tmp \
  --model gpt-5.4 --effort low \
  </dev/null >/tmp/codex-probe.stdout 2>/tmp/codex-probe.stderr & disown

# 3. From the Claude Code session, try:
#    SendMessage(to="codex-probe")
#    → fails with "No agent named 'codex-probe' is currently addressable"
```

## Root cause hypothesis

Claude Code's leader maintains an in-memory `addressable agents` list, separate from the on-disk team config. That list is populated when the leader spawns teammates via its OWN spawn flow (Agent tool, Agent Teams TUI, or the `CLAUDE_CODE_TEAMMATE_COMMAND` shim hook). It is NOT populated by:
- The team config file being mutated externally
- A separate process self-registering itself in the team config
- A new inbox file appearing in the team's `inboxes/` directory

This is the same architectural pattern that gates TUI presence: the leader trusts its own spawn callbacks, not the filesystem.

## Why this matters for claude-anyteam

The README documents two ways to launch a Codex teammate:

1. **Leader-spawned via shim** (recommended for normal use) — the leader spawns it, so the leader knows about it; SendMessage works fine
2. **External launch** (`setsid nohup claude-anyteam ...`) — for headless / persistent background adapters

We documented mode (2) as "messageable but not in TUI". That was incomplete. The full truth is: **mode (2) is messageable from peers but NOT from the leader's `SendMessage` tool**. The leader can still receive messages from the adapter (the adapter writes to the leader's inbox file directly, which works), but cannot initiate messages to it via the protocol's normal entry point.

## Workaround used during the vendoring task

When the leader could not reach `codex-vendor3` via SendMessage, we wrote the message directly to the adapter's inbox file:

```python
import json, time
from pathlib import Path

inbox = Path.home() / ".claude/teams/{team}/inboxes/{name}.json"
msg = {
    "from": "team-lead",
    "summary": "...",
    "text": "...",
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    "read": False,
}
existing = json.loads(inbox.read_text()) if inbox.exists() else []
existing.append(msg)
inbox.write_text(json.dumps(existing, indent=2))
```

The adapter picked up the message on its next poll (1.5s later) and processed it correctly. The protocol layer is fine — the only broken thing is the leader's outbound routing.

## Possible fixes

1. **Adapter pings the leader at registration time.** When a new adapter self-registers in `config.json`, it could write a short `agent_announced` message to every other member's inbox (including team-lead). The leader's session-handler could then add the agent to its addressable list when it sees that message.

2. **Leader watches the team config file.** Add an inotify (or polling) watcher to the leader for `~/.claude/teams/{team}/config.json`. When a new member appears, register them in the addressable list. This is upstream Claude Code work, not adapter work.

3. **Provide an "address by agent ID" entry point.** Allow the leader to send messages by raw `agentId` rather than going through the addressable-list lookup. This is also upstream Claude Code work.

4. **Document the workaround prominently.** Until #1 or #2 lands, surface the inbox-write workaround in the docs so users running headless adapters know how to bridge messages from the leader.

## Owner

Need to file an issue on the claude-anyteam repo and link it from `docs/roadmap.md` under "known issues". Approach #1 (adapter announces itself) is the most actionable for us; approaches #2 and #3 are upstream-Claude-Code work that we can advocate for but cannot land ourselves.
