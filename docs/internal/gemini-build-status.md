# Gemini adapter build — status

## 2026-04-23 — Kickoff

**Branch:** `gemini-adapter`
**Plan:** Plan A (headless `gemini -p ... --output-format stream-json`)
**Team:** `gemini-build` (all `gpt-5.5 xhigh`)

### Team and task assignments

| Owner | Starting task(s) | Area |
|---|---|---|
| `codex-lead` (me) | #14 (status doc), merge-gate review | coordinator |
| `codex-researcher` | #1 | live probes of installed `gemini` binary → `docs/internal/gemini-live-research.md` |
| `codex-shared` | #2, #3, #4, #5, #7 | registration generalize, backends skeleton, shim route, env vars, console script |
| `codex-gemini-invoke` | #8 (blocked on #1, #2, #3) | `backends/gemini/config.py` + `invoke_exec.py` |
| `codex-gemini-loop` | #9 (blocked on #3), then #10, #11 | prompts, forked loop, CLI entry point |
| `codex-tester` | #12 (blocked on impl) | 9 test files, 20+ new tests |
| `codex-reviewer` | #13 (blocked on #12, #11) | 6-correction audit + full suite |

### First-wave start (parallel)

- `codex-researcher`: task #1.
- `codex-shared`: task #2 first (needed by T-I1 before the `GeminiSettings` shape is finalized), then #3, #4, #5, #7 in any order.

### Merge gates (lead enforces)

- 6 critical corrections all visibly addressed (reviewer confirms).
- 20+ new passing Gemini tests, total ≥ 222.
- Zero Codex-side regressions.
- No Codex branding in Gemini registration (hard block).
- Live battle-test report filed.

---

## 2026-04-23 — Research §4 closed; install authorization requested

- **§4 (auth cache layout)** answered empirically from the cached `~/.gemini/` on this host. Key findings: `projectHash = sha256(abs_cwd)` (confirmed at byte level), `oauth_creds.json` is mode 600 (adapter must preserve), no absolute paths leak outside `$HOME/.gemini/` → `HOME` override is cache-isolation-clean.
- **§4.5 (OAuth first-run behavior, source read)** committed (`a4148f0`). Source-based, marked "needs binary confirmation."
  - **Key finding #1:** `NO_BROWSER=true` in the probe's env turns a missing `oauth_creds.json` into an immediate `FatalAuthenticationError` instead of a 5-min browser-callback hang. Probe needs no custom timeout wrapper.
  - **Key finding #2:** Seeding the adapter home with `oauth_creds.json` alone is NOT sufficient. `validateNonInteractiveAuth` resolves `settings.security.auth.selectedType` before consulting creds. Must copy BOTH `oauth_creds.json` AND `settings.json` from the real `~/.gemini/`.
  - Auth precedence order (from source): `GOOGLE_GENAI_USE_GCA` → `GOOGLE_GENAI_USE_VERTEXAI` → `GEMINI_API_KEY` → Cloud Shell/ADC. Without `settings.selectedType`, env vars win over cached OAuth.
- **§4.7 — Q1(c) probe pseudocode** drafted, ready for `codex-gemini-loop` to consume when T-L3 starts. ~40 lines, 10 s total budget, three modes: `api_key` / `oauth_seeded` / `blocked`.
- **§§1, 2, 3, 5 still BLOCKED** on installed-binary access. User authorization for `npm i -g @google/gemini-cli` requested (message sent to team-lead).
- codex-researcher redirected to draft the pseudocode and continue doc-level source reads while waiting for install.

### Current bottleneck

`codex-gemini-invoke` (task #8, transport layer) will hard-block on empirical §§1–3 the moment they pick it up. That's the first teammate whose task can't start without the binary. Everything else (shared-infra, Gemini prompts) can proceed in parallel.

_Next milestone update: install authorization + wave 1 shared infra status._
