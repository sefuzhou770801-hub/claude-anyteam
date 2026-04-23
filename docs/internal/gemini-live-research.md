# Gemini CLI live research

_Author: `codex-researcher` (gpt-5.5 xhigh)_
_Started: 2026-04-23_
_Scope: empirical findings from the installed `gemini` binary on this host. Complements `docs/internal/gemini-research-official.md` and `docs/internal/gemini-research-reverse.md`, which are doc/source-based. **Where this file disagrees with those, this file wins for implementation decisions** (binary is ground truth)._

## Status at a glance

| # | Brief question | Status |
|---|----------------|--------|
| 1 | `gemini --version` / `gemini --help` capture | BLOCKED — binary not installed. Install approval requested from `codex-lead`. |
| 2 | `stream-json` event shape probe | BLOCKED — requires installed binary. |
| 3 | `--resume` / `--acp` / `--experimental-acp` support | BLOCKED — requires installed binary. |
| 4 | OAuth session cache — storage + `HOME`-override behavior | **Answered** (empirical, see §4). |
| 5 | MCP tool-name prefix convention on installed version | BLOCKED — requires installed binary. |

Entries below are filled in as they land. Treat BLOCKED rows as _not yet verified_ — implementers must not assume the doc-based research is correct for those items.

---

## 0) Install state on this host (2026-04-23)

- `which gemini` → not found. `PATH` includes `~/.local/bin`, `/usr/local/bin`, Windows `npm` interop, and plugin dirs — none contain `gemini`.
- Full filesystem find (`find / -name gemini -type f`, `-type l`) returned nothing outside `/proc`.
- `npm ls -g --depth=0` at `/usr/local/lib` shows only:
  - `@anthropic-ai/claude-code@2.1.114`
  - `@openai/codex@0.124.0`
  - `claude-anyteam@0.1.0`
- No `@google/gemini-cli` or equivalent package is installed globally.
- `npx --yes @google/gemini-cli --version` was attempted and **denied by policy** ("Code from External / Untrusted Code Integration without explicit user authorization").

**Conclusion:** the binary has been run on this host previously (auth cache exists and was refreshed today, 2026-04-23) but the CLI is not currently installed. A lead-approved install is required before §§1/2/3/5 can be filled in.

---

## 1) `gemini --version` / `gemini --help` — BLOCKED

Pending install. Will capture verbatim stdout/stderr when available, with version number, short-flag vs long-flag shape (`-p` vs `--prompt`, `-r` vs `--resume`, etc.), and the exact spelling of `--output-format` choices.

## 2) `stream-json` event shape — BLOCKED

Pending install. Will run a trivial `gemini -p 'say hello' --output-format stream-json` and record:
- the precise JSON shape of `init`, `message`, `tool_use`, `tool_result`, `result`, `error` events
- presence/absence of any events not enumerated in `gemini-research-official.md §1`
- whether `session_id` is delivered on `init` (plans doc §"Plan A" assumes yes; `codex.py::run` needs this to return in `CodexResult.session_id`)
- whether non-JSON stderr noise leaks to stdout (the tolerant-parser requirement in feasibility doc §"Adjusted risks")

## 3) `--resume`, `--acp`, `--experimental-acp` — BLOCKED

Pending install. Note the doc mismatch already flagged in `gemini-research-official.md §8` and `gemini-research-reverse.md §3.2`: ACP page uses `--acp`, cheatsheet still shows `--experimental-acp`. Installed-binary check is the only way to settle which the `feature_test()` probe must accept. Expect to try both and record which is rejected with what error.

## 5) MCP tool-name prefix — BLOCKED

Pending install. Plans doc assumes `mcp_<server>_<tool>` (hence prompts must reference `mcp_anyteam_send_message`, not `send_message` — feasibility doc critical correction #5). Need to verify empirically by registering a dummy MCP server and observing the tool-name that Gemini advertises.

---

## 4) OAuth session cache — storage + `HOME`-override behavior

**Answered empirically** from the live `~/.gemini/` tree on this host. The binary was authed today (2026-04-23, `oauth_creds.json` last modified 15:46 UTC) before being uninstalled or via `npx`.

### 4.1 Directory layout (what exists on disk after an OAuth session)

```
~/.gemini/
├── google_accounts.json        # {"active": <email>, "old": [...]}
├── installation_id             # anonymous install UUID
├── oauth_creds.json            # mode 600; keys: access_token, refresh_token, id_token, expiry_date, scope, token_type
├── projects.json               # {"projects": {"<abs_cwd>": "<project_slug>"}}
├── settings.json               # {"security": {"auth": {"selectedType": "oauth-personal"}}}
├── state.json                  # UI nudge counts, banner counters — not security-relevant
├── trustedFolders.json         # {"<abs_cwd>": "TRUST_FOLDER"}
├── history/
│   └── <project_slug>/
└── tmp/
    ├── bin/rg                  # bundled ripgrep binary (shipped by the CLI itself)
    └── <project_slug>/
        ├── .project_root       # literal absolute path of the project
        ├── logs.json           # [{sessionId, messageId, type, message, timestamp}, ...]
        └── chats/
            └── session-<YYYY-MM-DDTHH-MM>-<short_uuid>.jsonl
```

**Permissions on credential files:** `oauth_creds.json` is mode 600 (`-rw-------`). `settings.json`, `google_accounts.json`, and the rest are 644. The CLI relies on filesystem mode to protect the refresh token — the adapter's wrapper-config generator must not `chmod 644` anything under a Gemini home it creates.

### 4.2 Project-hash derivation — `sha256(abs_cwd)`

The per-project subdirectory name in `~/.gemini/tmp/` is the short slug from `projects.json` (e.g. `nebius-inference`), **not** the hash. But the JSONL session lines contain a `projectHash` field, which is `sha256(abs_cwd)` of the cwd at the time the session started.

Empirical confirmation on this host:

```
abs_cwd = "/home/rosado/Projects/Nebius Inference"
sha256  = 0ab9c6641cc575f6e8f43b7adb2f6923584df69a748a43accefc4059ee580e78
stored  = 0ab9c6641cc575f6e8f43b7adb2f6923584df69a748a43accefc4059ee580e78   ← matches
```

Implication: if the adapter runs Gemini with `HOME=<adapter_home>` but keeps `cwd` set to the real repo (which feasibility doc critical correction #1 requires), Gemini's session continuity for that repo lives at `<adapter_home>/.gemini/tmp/<slug>/chats/`, keyed by `sha256(abs_cwd)`. A different cwd → different hash → no session reuse. This is the per-project scoping the official doc §4 claims, now confirmed at the on-disk layer.

### 4.3 Session file shape

One JSONL file per session at `.gemini/tmp/<slug>/chats/session-<timestamp>-<short_uuid>.jsonl`. First line is the session header:

```json
{"sessionId":"2a07c579-7101-42a3-9f59-ba98dfec56a9","projectHash":"<sha256>","startTime":"...","lastUpdated":"...","kind":"main"}
```

Subsequent lines are message entries (`{"id":..., "timestamp":..., "type":"user","content":[{"text":"..."}]}`) interleaved with `{"$set":{"lastUpdated":"..."}}` update markers — a log-structured append pattern.

The UUID in the filename matches the `sessionId` inside the file (first 8 chars of the UUID), so session IDs are discoverable from filenames without parsing JSON. That matters for the adapter's `--resume <id>` call — the ID the adapter gets from the `init` event should be usable directly.

### 4.4 `HOME`-override behavior — the critical finding

The feasibility doc's critical correction #1 requires the adapter to point Gemini at an adapter-owned home (`~/.claude/teams/<team>/state/<agent>/gemini/`). On-disk evidence says this **works cleanly** because everything Gemini caches — creds, project slugs, session history — is rooted at `$HOME/.gemini/`. No absolute paths outside `$HOME/.gemini/` appear in any of the files inspected on this host.

**However — critical correction #2 is real and essential:** the wrapper MCP server (`claude_teams` / `claude-anyteam-wrapper`) reads `Path.home()` to find `~/.claude/teams/<team>/...`. If Gemini launches the wrapper with the overridden `HOME=<adapter_home>`, the wrapper will look under `<adapter_home>/.claude/teams/...` which does not exist. Plans doc §"Plan A" and feasibility doc §"Critical corrections #2" both say to solve this by passing `env: {"HOME": <real_home>}` in the `mcpServers.anyteam` entry. This matches Gemini's documented server env handling (reverse research §4.2: "Gemini treats explicitly configured server env vars as trusted and does not redact them").

**Open sub-questions (to verify on the installed binary):**

1. Does Gemini **prefer** `oauth_creds.json` silently over `GEMINI_API_KEY` when both are present? Reverse research §6.5 says env precedence can go wrong with Vertex auth. The Q1(c) startup probe in `codex-gemini-loop`'s brief needs to know the actual precedence order — I cannot determine this from on-disk evidence alone.
2. When `HOME` is overridden and the adapter home's `.gemini/` is **empty** (first-run from a clean state), does Gemini attempt interactive OAuth? Reverse research §1.3 says containerized OAuth needs `-d` for URL copy. The adapter must fail-fast with a clear message (plans doc brief §"codex-gemini-loop" item 3), not block waiting for a browser callback that will never come.
3. Does the OAuth refresh_token get rewritten to the overridden HOME, or does Gemini follow a symlink / env override back to the real `~/.gemini`? I expect the former (everything is rooted at `$HOME/.gemini/`), but this must be confirmed.

### 4.5 Implication for Q1(c) startup auth probe

The startup probe (`codex-gemini-loop` brief item 3) is described as: "try whatever the installed binary's signed-in session provides first; if that fails and `GEMINI_API_KEY` is set, use it; if both fail, error out."

On-disk evidence suggests the cleanest implementation is:

1. **Seed-then-test strategy.** On first use, if `<real_home>/.gemini/oauth_creds.json` exists and no `GEMINI_API_KEY` is set, **copy** the auth-relevant files (`oauth_creds.json`, `google_accounts.json`, `settings.json`) into the adapter home before launching Gemini. Then run a trivial `gemini -p 'ok' --output-format json` probe; if it exits 0, auth is live.
2. If `GEMINI_API_KEY` is set, skip the copy and let env-var auth take precedence — but note (open sub-question 1) that we need to verify Gemini actually prefers the env var over cached OAuth in the same home. If it does not, the adapter must ensure `oauth_creds.json` is absent from the adapter home when API-key mode is desired.
3. If neither works, the adapter must error out with a message naming both paths, not hang waiting for a browser OAuth that cannot complete in subprocess mode.

This is implementation guidance, not a hard recommendation — I'll revise after the installed-binary probe confirms precedence order.

---

## Revision log

- 2026-04-23 — initial doc. Blocked on install approval for §§1/2/3/5. §4 answered empirically from on-disk state; §4.5 open sub-questions flagged for installed-binary follow-up.
