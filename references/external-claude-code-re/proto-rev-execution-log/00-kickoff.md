# Proto-rev impl + stress test execution log

**Started:** 2026-04-27T06:14Z (user is sleeping; 72hr autonomous run)
**Branch:** `proto-rev/impl-2026-04-27` off main
**Commit at kickoff:** research-package landing (CLAUDE.md three north stars + 10 protocol-rev docs + prototype-sdk).

## Plan

**Phase 1 (T+0 → T+24h): foundation implementation.**
- R1+R2: schemas as importlib resources + pydantic agent_type default. Hard prereq for everything else.
- R3+R10: typed `messageKind` discriminator + `schema_version` on payloads.
- R6: `shutdown_approved`/`shutdown_rejected` wire migration matching v2.1.119 host catalog.
- R11: capability declaration with cheap flags on TeammateMember.

**Phase 2 (T+24h → T+48h): visibility + peer fidelity.**
- R16: B9 §6 event envelope + append-only event log (THE measurement substrate).
- R12+R13: eager capability-manifest broadcast at team formation + wrapper MCP tool.
- R14: peer-capability prompts auto-injected into peers' system prompts.
- R15+W3+W7: peer-DM consistency audit + Gemini PR-#11/#12 guard parity.

**Phase 3 (T+48h → T+72h): stress test + iterate.**
- Spawn stress-test team: opus-stress-analyst + 3 codex eval-harness builders + actual targets (codex-tgt + gemini-tgt + kimi-tgt at top model/effort).
- Run scenarios across team configs: homogeneous-codex, homogeneous-claude, heterogeneous-mixed (Codex + Gemini + Kimi + native-Claude).
- Measure via R16 event envelope. Compute throughput, cross-peer ratio, capability invocation count, error rate, idle time.
- Iterate: feed findings back into protocol refinement.

## Initial team (T+0)

| Role | Name | Model |
|---|---|---|
| Architect | opus-arch-impl | Opus 4.7 |
| Reviewer | opus-rev-impl | Opus 4.7 |
| R1+R2 schemas + pydantic | codex-impl-foundation | gpt-5.5 / xhigh |
| R3+R10 messageKind + schema_version | codex-impl-msgkind | gpt-5.5 / xhigh |
| R6 shutdown migration | codex-impl-shutdown | gpt-5.5 / xhigh |
| R11 capability declaration | codex-impl-cap | gpt-5.5 / xhigh |
| R16 event envelope + log | codex-impl-events | gpt-5.5 / xhigh |
| R12+R13 manifest broadcast | codex-impl-broadcast | gpt-5.5 / xhigh |
| R15+W3+W7 peer-DM consistency | codex-impl-peer | gpt-5.5 / xhigh |
| R14 peer-capability prompts | codex-impl-prompts | gpt-5.5 / xhigh |

8 codex + 2 opus = 10 teammates. 4:1 codex:opus, slightly above the 3:1 target — codex is doing heavy implementation lifts; opus is architect+reviewer.

## Safety rails

- Branch only: `proto-rev/impl-2026-04-27`. No pushes to main. No force pushes. No tags.
- Test gate: every commit must pass `uv run pytest` (633 tests). Implementers run locally before requesting review; opus-rev-impl runs full suite as a final gate.
- No deletions of vendored references or research artifacts under `docs/internal/protocol-rev/` or `references/external-claude-code-re/`.
- Vendored externals (clawcode, cs50victor pending, etc.) intentionally not committed (nested git repos); locally available as research material.

## Cadence

- Lead checkpoints every 60-90 minutes via ScheduleWakeup.
- Each checkpoint: read inbox for teammate updates, check git log for new commits, check test status, steer/respawn as needed, schedule next.
- Daily summary appended below.

## Updates

(appended as the run progresses)
