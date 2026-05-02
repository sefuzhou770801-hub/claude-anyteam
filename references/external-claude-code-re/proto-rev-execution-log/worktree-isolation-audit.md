# Worktree isolation audit (#48)

Date: 2026-04-28  
Branch: `proto-rev/impl/worktree-isolation-audit`  
Integration anchor: `b0e226a`

## Question

A prior swarm run left `proto-rev/impl/visibility-tail-extensions` with a
`schema: add inbox attachment metadata` commit that also appeared in
`proto-rev/impl/long-output-attachment` with different SHAs and matching
`AuthorDate` second (`2026-04-28T08:35:33-04:00`). The concern was that two
Codex teammates may have shared a working directory or Git index.

## Code-path findings

### Spawn cwd path

`src/claude_teams/spawner.py` records the caller-provided `cwd` on the
`TeammateMember` and builds the tmux command as:

```text
cd <member.cwd> && CLAUDECODE=1 CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 <claude> --agent-id ...
```

`src/claude_anyteam/spawn_shim.py` routes `codex-*` teammates to the
`claude-anyteam`/`codex-teammate` adapter, but it does **not** forward a
`--cwd` argument. The adapter therefore gets its cwd from the process cwd left
by the tmux `cd`.

`src/claude_anyteam/config.py` resolves settings cwd from CLI `--cwd`, env, or
`os.getcwd()`; for spawned Codex teammates in this path, `os.getcwd()` is the
`member.cwd` directory selected by the spawner.

### Codex cwd handling

The installed Codex wrapper is `codex-cli 0.124.0`. Its `exec` help says
`-C, --cd <DIR>` tells the agent to use the specified directory as its working
root.

The adapter uses both cwd channels depending on mode:

- legacy exec mode: `src/claude_anyteam/codex.py` passes `codex exec ... -C
  <cwd>`; the subprocess itself inherits the adapter cwd.
- app-server mode: `AppServerClient` starts `codex app-server` inheriting the
  adapter cwd, then `app_server_invoke()` sends `thread/start` with
  `{"cwd": str(cwd), ...}`.

There is no Codex-specific Git index. Git isolation is entirely determined by
what Git worktree `cwd` resolves to.

## Incident evidence

Reflogs show the schema commit originated on the UI branch, then was
cherry-picked onto the long-output branch:

```text
proto-rev/impl/visibility-tail-extensions@{2026-04-28 08:35:33 -0400}: commit: schema: add inbox attachment metadata
proto-rev/impl/long-output-attachment@{2026-04-28 08:36:07 -0400}: cherry-pick: schema: add inbox attachment metadata
```

After later rebases, the visible commits are:

```text
35ab99b parent d227bff AD=2026-04-28T08:35:33-04:00 CD=2026-04-28T08:35:33-04:00
4cfec10 parent a3f5b84 AD=2026-04-28T08:35:33-04:00 CD=2026-04-28T08:42:35-04:00
```

So the duplicate SHA/content is best explained by an explicit cherry-pick (and
later rebase preserving author date), not by accidental index bleed.

## Reproduction

Scratch repro: `/tmp/worktree-isolation-repro`.

Two real Git worktrees from the same repo produced distinct indexes:

```text
A_INDEX=/tmp/worktree-isolation-repro/repo/.git/worktrees/wt-a/index
B_INDEX=/tmp/worktree-isolation-repro/repo/.git/worktrees/wt-b/index
```

Running simultaneous `git add` + delayed `git commit` in those worktrees was
isolated:

```text
A_FILES=a.txt
B_FILES=b.txt
A_STATUS=0
B_STATUS=0
```

Negative control in the same worktree shared `.git/index` and hit the expected
lock/contamination hazard:

```text
fatal: Unable to create '/tmp/worktree-isolation-repro/repo/.git/index.lock': File exists.
SHARED_INDEX=.git/index
SHARED_STATUS=?? shared-b.txt|
```

## Root cause assessment

- **No evidence found** that Codex itself shares a hidden parent Git index when
  given distinct real worktree directories.
- **Evidence found** that this substrate currently trusts the caller-provided
  `cwd` and does not create or enforce an isolated Git worktree per teammate.
  If two routed teammates are spawned with the same repo root, or with
  different subdirectories inside the same worktree, Git will discover the same
  worktree and share the same `.git/index`.
- The observed duplicate commit specifically appears to be a cherry-pick, but
  the substrate still has a real protocol-edge gap: §1/§3 isolation is
  advisory rather than enforced.

## Fix direction

Close the substrate gap in `claude_teams.spawner.spawn_teammate`: when a caller
passes an explicit `cwd` inside a Git repo whose top-level is already used by
another team member (including `team-lead`), materialize a unique real `git
worktree` for the new teammate and launch the teammate there. If the caller
already passed a distinct worktree path, leave it unchanged.

The regression invariant should assert:

1. colliding requested cwd is rewritten to a real isolated worktree;
2. original and isolated cwd have different `git rev-parse --git-path index`
   paths;
3. concurrent commits in original and isolated worktrees do not
   cross-contaminate commit file lists or leave dirty status.
