# Session closeout summary — 2026-04-28

## Integration delta

The requested pre/post anchor was `eff6a3d` → `2900940`; that measured range contains **46 commits**, **59 unique files changed**, **101 added/expanded test definitions** across **9 new test files**, and **+8,929/-396 LoC** in the final diff (net **+8,533**, churn **9,325**; the per-commit `git log --shortstat` sum is 133 file-touches, +9,013/-480). During this closeout window, #56, #54, and #53 also landed, so the live integration head before this artifact was `f917481` with **51 commits**, **61 files changed**, **105 test definitions added**, and **+9,748/-396 LoC** over `eff6a3d`.

This #55 artifact is intentionally doc-only. After it is fast-forward merged, the final range is **52 commits**, **62 files changed**, **105** test definitions added, and **+9,816/-396 LoC** (**10,212** changed LoC, **+9,420** net LoC) over `eff6a3d`. The #55 row below is this doc-only commit; its exact SHA is the fast-forward merge head recorded by git after landing.

## Per-task closures

| task_id | subject | owner | commit_sha |
|---|---|---|---|
| D1-fix | pid-aware sandbox marker prevents concurrent-run wipe | team-lead / stress lane | `3965518` |
| 14 | Gemini ACP honors `messageKind` steer discriminator | earlier impl lane | `bee20bd` |
| 19 | Kimi honors `messageKind` steer discriminator | codex-pair-a | `6ddc157` |
| 20 | Stress runner SIGTERM hardening with `aborted` marker state | codex-pair-b | `e7a7a90` |
| 27 | SendMessage visibility invariant in wrapper + routed prompts | codex-pair-a | `013ed15`, `e184389` |
| 28 | event-driven inbox watcher / fs-watch lift | codex-pair-a; salvaged by team-lead after timeout | `d227bff` |
| 30 | capability-manifest prewarm | codex-substrate | `04221b4` |
| 31 | visibility-tail filesystem UI projector | codex-trace | `1cd9f56` |
| 32 | expose `protocol_tools` discovery via `read_config` | codex-substrate | `c5476fc` |
| support | wrapper grep tool for teammate forensics | team-lead | `e3622fa` |
| 33 | typed lifecycle payloads + `messageKind` stamping | codex-pair-a | `758dfe1`, `527b0fd`, `a3f5b84` |
| 34 | manifest-gated peer steer across Codex/Gemini/Kimi | codex-architect | `a4a4fe0`, `42e8d75` |
| 35 | per-target micro-batched inbox writer / 50 ms debounce | codex-pair-a; landed by team-lead after flap | `5106f6d` |
| 36 | long-output inbox attachment protocol | codex-perf | `4cfec10`, `55ff9b1`, `f5e5e94`, `e9b65f6`, `97dfabe` |
| 37 | visibility-tail JSON/filter/color/long-card extensions | codex-ui | `5cfa508`, `122c29c` |
| 38 | Codex App Server transport reconnect/resume recovery | codex-pair-b | `14c8a0a`, `a2829c7`, `b0e226a` |
| 39 | bounded capability-prewarm fan-out | codex-perf | `a6e7d29`, `9e896bf` |
| 40 | capability hook registry + manifest schema validation | codex-architect | `b225f0d`, `b50feb7` |
| 41 | delegated parent task linkage + batch summary emit tool | codex-architect; second commit salvaged by team-lead | `5d32a2c`, `83c98a9` |
| 42 | D1/#20/#28 30-minute paired-codex stress validation | codex-pair-b | run artifact / `d1-validation-final.md` (untracked operational artifact) |
| 43 | 60-minute paired-codex stress validation | codex-pair-b | run artifact / `d1-validation-final.md` (untracked operational artifact) |
| 44 | wrapper MCP diagnostics for SendMessage tool-discovery flap | codex-prompts | `2900940` |
| 45 | checkpoint commits + turn-timeout mitigation | codex-resilience | `87c32a0`, `1fd1fe2`, `436854d`, `accc001` |
| 46 | capability matrix audit + complete backend declarations | codex-audit | `3786da7`, `08d8341` |
| 47 | architecture docs for harness, visibility, peer-efficiency substrate | codex-docs | `b2a41e5`, `c8c0e30`, `9133e15` |
| 48 | worktree-isolation audit + colliding-cwd guard | codex-substrate-audit | `9f66d8d`, `8083fe5`, `e5a4469` |
| 53 | WebSocket variant of visibility-tail | codex-ws | `e8ee846`, `154188e`, `f917481` |
| 54 | update comparison matrix with actual session outcomes | codex-matrix | `af1ff60` |
| 56 | post-everything full-suite and smoke sweep | codex-sweep | `05bed34` |
| 55 | session closeout artifact | codex-changelog | this doc-only commit (FF-merge head) |

Research tasks #21-#26 and #29 also completed before the integration range: codex-clawclone (`clawcode.md`), codex-clones (`maorinka-claude-rs.md`), codex-extract (`piebald-system-prompts.md` plus `_comparison-matrix.md`), codex-priorart (`nwyin-cleanroom.md`), codex-substrate (`pickle-pixel-hydrateams.md`), and codex-trace (`aproto-codex-bridge.md`). Those research-digest artifacts were present in the working tree during the session but were not part of the tracked `eff6a3d..` integration delta.

## Roster summary

The research swarm was `codex-clawclone`, `codex-clones`, `codex-extract`, `codex-priorart`, `codex-substrate`, and `codex-trace`. It produced the six external-codebase digests plus the first comparison matrix; final DMs cluster around 2026-04-28 02:53-03:05Z, with `codex-substrate`/`codex-trace` continuing into #30/#31/#32 until roughly 03:25Z. By the 15:16Z third-wave config snapshot those research teammates were no longer live members, so their shutdown/removal happened before the third-wave spawn.

The implementation roster shipped in waves. `codex-pair-a` handled #19/#27/#33 and provided first-pass work later salvaged for #28/#35; `codex-pair-b` handled #20/#38 and launched/closed the #42/#43 stress validations; `codex-architect` handled #34/#40 and started #41 before timeout salvage; `codex-perf` shipped #36/#39; `codex-ui` shipped #37. The second wave (`codex-prompts`, `codex-resilience`, `codex-audit`, `codex-docs`, `codex-substrate-audit`) shipped #44-#48. The third wave registered at 2026-04-28 15:16Z: `codex-stress` launched #49 at 15:19Z, `codex-ablation` launched #52 at 15:21Z, `codex-flap-fix` reported diagnostic data and a fix proposal by 15:23Z, `codex-matrix` closed #54 at 15:23Z and shut down at 15:24Z, `codex-ws` closed #53 at 15:26Z, `codex-sweep` closed #56 and received shutdown approval at 15:23Z, while `codex-collisions` and `codex-changelog` remained active at this snapshot.

## Failure modes + handling

The recurring SendMessage tool-discovery flap persisted even after #27's visibility-invariant prompts and #32's `read_config.protocol_tools` discovery. #44 added wrapper MCP diagnostics, and #51's early diagnostic run on 2026-04-28 found fresh Codex App Server turns listing all 17/17 expected tools with `send_message_registered=true`; the working hypothesis moved toward invalid model prose needing a repair/guard path rather than actual MCP registration loss.

The 900s Codex App Server turn timeout reaped uncommitted work at least twice: pair-a's #28 first pass and architect's #41 second commit. Handling evolved into a salvage pattern (inspect the teammate worktree, recover edits, commit from the lead lane) and then into #45's durable mitigation: configurable timeout coverage, a wrapper `checkpoint_commit` git tool, prompt guidance to checkpoint during multi-file work, and an E2E test proving checkpoint survival across simulated timeout. False `task_complete` payloads with empty `files_changed` also appeared (#31/#41-style operational noise from codex CLI turn-end auto-emits), so the closeout rule is to verify git status and integration HEAD before trusting completion claims. The duplicate-schema-commit incident was investigated in #48 and turned out to be a cherry-pick, not substrate bleed; the final guard still isolates colliding teammate cwd values into real git worktrees.

## Empirical findings

`d1-validation-final.md` validates substrate stability: the 60-minute paired-codex run (`S6-W7-20260428T1326Z-postall-3600s`) had M5 = 0.000, `visibility_degraded_count = 0`, harness-preservation violations = 0, and S1 flatten violations = 0. The combined D1 + #20 + #28 + #30/#32/#35/#38/#44 stack therefore looked operationally stable under stress; the limitation was not a crash or protocol break.

Throughput was coordination/model-bound rather than budget-bound. Doubling paired-codex W7 budget from 30 to 60 minutes only moved completions from 3/15 to 4/15, while M11b p95 turn duration reached 1156s (~19 minutes/turn). M11a max latency improved sharply (post-16 baseline 587.6s → 458.8s at 30m → 136.6s at 60m), consistent with inbox-wake/prewarm/debounce improvements, but paired-codex produced only four M11a samples; the S8 kimi-codex follow-up is still the right scenario for a properly sampled M11a p95.

## Outstanding follow-ups

At assignment time the six open follow-up task IDs were #49-#54. As of the 2026-04-28 15:26Z snapshot for this artifact, #53 closed at `f917481` and #54 closed at `af1ff60`, so the remaining open workstreams are: #49 S8 kimi-codex 60-minute stress run (launched as `S8-W7-20260428T1519Z-postall-3600s`, awaiting scorecard/final verdict), #50 M13 collision investigation/fix (analysis commit `d3a3a1c` found the five rows are mostly scorer false positives and runtime fixes are in progress), #51 SendMessage flap fix (diagnostics collected; repair proposal in progress), and #52 ablation stress run (launched as `S8-W7-20260428T1521Z-postall-3600s-ablation`, awaiting final comparison).

The recently closed follow-up lanes should still be accounted for in downstream synthesis: #53 added the WebSocket visibility-tail projection with full-suite validation (`1002 passed, 2 deselected`), #54 updated the comparison matrix with actual session outcomes, and #56 verified the cumulative stack with `998 passed, 2 deselected, 1 warning`, skipped ruff/mypy because there was no config, and smoke-checked the #27/#28/#32/#40/#41 invariants. Once #49/#52 scorecards land, append them to the D1 validation narrative so M11a and ablation claims do not rely on the low-sample paired-codex evidence.
