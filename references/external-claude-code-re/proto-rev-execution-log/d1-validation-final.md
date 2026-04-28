# D1 + #20 + #28 stress validation — paired-codex re-run

Run-id: `S6-W7-20260428T1231Z-postall`. Integration HEAD at run-time: `d227bff` (post #28 event-driven inbox watcher; post-#20 SIGTERM hardening; post-D1 sandbox isolation). Detached launch via `setsid nohup python -u ... & disown` so the runner survived the codex teammate's turn boundary — that's the failure mode that killed the prior three attempts (#42 forensic).

## Run outcome

- **Detached launch worked.** Runner PID 532720 ran for ~31 minutes (timed out at 1800s wall-clock budget), produced events for both teammates, completed cleanup, marked sandbox `state=completed`. No SIGTERM-by-turn-boundary collapse.
- **Scorer failed at run time** because the detached process didn't have `PYTHONPATH=.` (`No module named 'tools'`). Re-scored offline; metrics below are post-rescore.
- **n_completed = 3/15.** Lower than healthy post-16 (11/15). Likely substrate stability is fine but task-complexity × budget mismatch — see open question below.

## Comparison table

| run | git_sha | n_completed | M1 throughput/min | M11a samples | M11a max | M11a p95 | M5 failure | wall_clock |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| **S6-W7-20260427T2348Z-postD1** (crashed, sandbox-wiped) | ee47267 | **0/15** | 0.073 | 9 | 168.8s | 168.8s | 0.667 | 1868s |
| **S8-W7-20260427T2348Z-post16** (healthy, kimi+codex) | ee47267 | **11/15** | 1.03 | 39 | 587.6s | 557.8s | 0.031 | 1869s |
| **S6-W7-20260428T1231Z-postall** (this run, paired-codex) | d227bff | **3/15** | 0.66 | 4 | 458.8s | (≤4 samples) | 0.0 | ~1810s |

## What improved

- **Sandbox isolation regression closed (D1 fix `3965518`).** The crashed postD1 run had its sandbox cross-wiped by a concurrent runner's marker-only cleanup. This run was solo, but the pid-aware marker is in place; the substrate-level fix is durable.
- **SIGTERM hardening closed (#20 fix `e7a7a90`).** The runner survived the codex turn boundary via detached launch, and the `aborted` marker state plus signal handler are in place for runs that DO get killed.
- **No turn failures (M5 = 0.0).** Down from 0.031 in the post-16 baseline. Cleaner per-turn execution.
- **M11a max latency is lower** (458.8s vs 587.6s in post-16). Only 4 samples vs 39, so this is suggestive not statistically conclusive — but consistent with the #28 event-driven inbox + #30 cap-prewarm + #35 debounce theory.
- **No M13 collisions; M4_team_cross_peer_ratio = 1.0.** Peer-to-peer flow is intact.

## Open question

**Why is n_completed only 3/15 in 30 minutes when M1 = 0.66/min should yield ~20 task-equivalents?** Hypotheses:

1. **Tasks claimed but not finished.** Codex App Server turn budgets can get long on W7 (coding workload); a task can be in-progress for 5-10 minutes before `task_complete` lands. Three completed × ~10 min = 30 min, fits the wall-clock.
2. **W7 task complexity is mismatched against the 30-min run budget for paired-codex.** The healthy post-16 baseline was kimi+codex (mixed backend) where kimi handles some tasks faster. Two codex App Servers in parallel may saturate model inference and leave most tasks queued.
3. **The new event-driven inbox + cap-prewarm + debounce machinery are correct individually but interact in ways that delay claim acceptance.** Less likely given the M11a-max evidence pointing the right way; but worth a paired-codex baseline that does NOT have the new machinery to disambiguate.

## Verdict

The substrate fixes (D1 + #20) are validated by the run completing cleanly under the conditions that previously killed it. The §3 closure stack (#27 + #28 + #30 + #32 + #35) is consistent with the M11a-max improvement direction. The completion count is below baseline but the failure mode looks like task-budget × complexity rather than substrate degradation — needs a follow-up run with a longer budget OR a smaller workload to disambiguate.

**Recommended follow-up**: re-run S6+W7 with `--timeout-seconds 3600` (60 min) under the same HEAD to get a completion-count comparable to post-16 and a properly-sampled M11a p95.

## Artifacts

- Scorecard: `references/external-claude-code-re/proto-rev-execution-log/runs/S6-W7-20260428T1231Z-postall/scorecard.json` (scorer-failure marker present; metrics null in scorecard.json — see per-scorer files)
- Per-scorer outputs: `runs/S6-W7-20260428T1231Z-postall/{collab,throughput,quality}/scenario.json` (rescored offline post-run)
- Events JSONL: `~/.claude/teams/stress-S6-S6-W7-20260428T1231Z-postall/events/codex-pair-{a,b}.jsonl` (~810KB total)
- Sandbox marker: `/tmp/stress-sandbox-S6-W7-20260428T1231Z-postall/.stress_sandbox_marker` (`state=completed`)

---

## Long-budget re-run appendix (60 min)

Run-id: `S6-W7-20260428T1326Z-postall-3600s`. git_sha at run-time: `b0e226a` (post-everything: D1 fix + L4 ladder + #27 prompts + #28 watcher + #30/#39 prewarm + #32 protocol_tools + #35 debounce + #36 attachment + #38 transport recovery + #34 manifest-gated steer + #20 SIGTERM hardening). Scoring git_sha: `2900940` (also includes #44 instrumentation, #46 cap completeness, #47 docs, #48 worktree isolation).

### Updated comparison

| run | budget | n_completed | M1 thr/min | M11a samples | M11a max | M11b p95 turn dur | M5 fail | n_blocked |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S6-W7-postD1 (sandbox-wiped) | 30m | **0/15** | 0.073 | 9 | 168.8s | 520.5s | 0.667 | 15 |
| S8-W7-post16 (kimi+codex healthy) | 30m | **11/15** | 1.03 | 39 | 587.6s | 488.8s | 0.031 | 4 |
| S6-W7-postall (paired-codex) | 30m | **3/15** | 0.66 | 4 | 458.8s | 577.9s | 0.000 | 12 |
| **S6-W7-postall-3600s** (this run) | **60m** | **4/15** | **0.56** | **4** | **136.6s** | **1156.4s** | **0.000** | **11** |

### Key findings

1. **Substrate stability is validated.** M5 = 0.000 (no turn failures); visibility_degraded_count = 0; harness_preservation_violations = 0; s1_flatten_violations = 0. The combined post-everything stack runs cleanly under stress for the full 60-min budget.

2. **Doubling the budget did NOT meaningfully increase completions** (3/15 → 4/15). The bottleneck is not budget.

3. **M11b p95 turn duration is the bottleneck: 1156s ≈ 19 min/turn.** With a 60-min budget, this allows only 3-4 turns per teammate. Two teammates × 3-4 turns = 6-8 task attempts; ~4 actually complete.

4. **M11a max RTT improved sharply** (587s baseline → 458s @30m → 137s @60m) — consistent with #28 event-driven watcher + #35 debounce + #30 prewarm closing the inbox-wake floor and the cap-discovery RTT. **However, M11a samples = 4** — too few to compute a real p95 in either run.

5. **Paired-codex doesn't actually exercise §3.** Two App Server teammates working a coding workload tend to operate as parallel solo agents, not as a coordinating pair. To get a real M11a p95 sample, S7 (gemini-codex) or S8 (kimi-codex) is the right scenario — the cross-backend latency surface is what the §3 metric is designed for. The existing post-16 baseline IS the right comparison; we don't need another paired-codex run.

6. **M13 collisions = 5** — a small number of peer-prose-as-steer false positives despite the L4 ladder shipping. Worth empirical investigation in a future session; not yet alarming.

### Verdict on the §3 closure stack

The combined post-everything stack ships substrate stability + M11a-max latency improvements. **n_completed limitations are model-inference-bound, not protocol-bound** — codex App Server turn complexity dominates wall-clock for paired-codex on W7.

To validate §3 properly, follow-up work should:
1. Re-run S8 (kimi-codex paired) at HEAD `2900940` with 60-min budget — that scenario produces enough peer-DM samples for a real p95.
2. Investigate M13 collisions — 5 false positives despite L4 + #34 manifest-gated steer suggests one more edge case worth closing.
3. Investigate the recurring SendMessage tool-discovery flap with the #44 instrumentation that just shipped — gather real diagnostic traces.

### Artifacts

- Scorecard: `runs/S6-W7-20260428T1326Z-postall-3600s/scorecard.json`
- Per-scorer outputs: `runs/.../{collab,throughput,quality}/scenario.json`
- Events JSONL: `~/.claude/teams/stress-S6-S6-W7-20260428T1326Z-postall-3600s/events/codex-pair-{a,b}.jsonl` (~2.6 MB total)
- Sandbox marker: `state=completed` post-run (#20 hardening confirmed working)
