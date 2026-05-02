# daily-1 — opus-arch-impl phase-1 status

**T+0 of arch role:** ~2026-04-27T07:30Z (post initial context load)
**Branch:** proto-rev/impl-2026-04-27
**Test baseline:** 633 (per test-status.log @ aa90de3)
**Items in scope (8 batched):** R1+R2 (#12), R3+R10 (#13), R6 (#14), R11 (#15), R16 (#16), R12+R13 (#17), R15+W3+W7 (#18), R14 (#19).

Out of this batch but flagged: R4 (filelock 30s timeout) is a soft prereq for R16's `events/.lock`. Recommend bundling a minimal R4 patch into R16's PR — without it a crashed teammate's stale `events/.lock` will block envelope writes for the next adapter startup. R7/R8 (backend_exit_code + TaskBlockedOut) deferred (out of scope this batch).

---

## 1. Lift-vs-adapt decisions for prototype-sdk → /src/ port

The prototype kit (`references/external-claude-code-re/prototype-sdk/agent_teams_kit/`) is research-grade and explicitly Phase-2 (K1–K4). For v0.7.0/0.7.1 we ship **discrete primitives that the kit will eventually formalize**, not the kit itself. Per-module ruling:

| Prototype module | Verdict | Why |
|---|---|---|
| `capabilities.py` (CapabilityEntry, CapabilityManifest, flat_capabilities, peer_prompt_fragment) | **LIFT shape, ADAPT wiring.** Place models + helpers in new `src/claude_anyteam/capabilities.py` (≤120 LOC). Pydantic shape, `flat_capabilities()` derivation, and `peer_prompt_fragment()` auto-generation are well-tuned and match 09 R11/R12/R14 verbatim. | The constant-token `failure_modes` validator is a §1-aligned sanity check — keep it. The `accepts_peer_steer` extra-field promotion is exactly the §3 / CD-6 hook R15 needs. |
| `events.py` (VisibilityEvent, VisibilityFlags) | **LIFT shape, ADAPT to spec catalog.** Place in `src/claude_anyteam/events.py` (≤80 LOC). Use 07 §7.2's full 10-kind closed Literal (prototype has 7 — extend to add `steer_ack`, `capability_changed`, `capability_manifest_updated`). Keep `payload: dict[str, Any]` for v0.7.0; per-kind sub-models land with R17–R19 (out of scope). | Closed Literal at the envelope level + open-dict payload matches 07 §7.1's "envelope normalizes for routing, not content" principle. |
| `storage.py` (TeamStorage Protocol + FilesystemStorage) | **DO NOT LIFT.** The Protocol abstraction is K1 phase-2. For now: add `append_event(team, agent, envelope)` and `read_events(team, agent, since_seq, limit)` directly into `src/claude_anyteam/protocol_io.py` mirroring `send_json_to_lead` style. | Lifting Storage now means refactoring every existing call site of `claude_teams.tasks` / `claude_teams.messaging` — large-blast-radius substrate change. Defer per 09 §2.4 D1/D2 discipline. |
| `teammate.py` (Teammate base class + emit_*) | **DO NOT LIFT.** This is K2. Production has three loops (Codex / Gemini / Kimi) with distinct shapes and lifecycle quirks. | Lifting now would force 3-way loop convergence which is exactly the "big-bang refactor" 09 §0.8 forbids. Instead, expose `emit_*` helpers as module-level functions in `protocol_io.py` (or `events.py`); each loop calls them at its own emit points. R17/R18/R19 (out of this batch) wire them per backend. |
| `team.py` (Team, find_capability, peer_prompt_fragments_for, broadcast_capability_manifest) | **ADAPT, do not LIFT verbatim.** The function signatures are good; lift the helpers as standalone functions in `src/claude_anyteam/capabilities.py`. **Reject the prototype's storage location for the cache** — it writes `peerManifestCache` inline under each member row in `config.json`, which bloats config (every cache update touches every member's row) and races with R5's shared config lock. R12 spec calls for per-teammate files at `~/.claude/teams/<team>/manifests/<agent>.json` — ship that. | Per §1: capability is per-harness; store per-harness. Per §3: avoid the n×n config bloat that capability-discovery latency was meant to fix. |
| `messages.py` (WirePayload base, kind discriminator) | **ADAPT, do not LIFT verbatim.** Production `_Base` uses `type` for most payloads and `kind` for `task_complete`/`task_blocked` only — that's an acknowledged wart (L11-bis / D12). Do NOT switch `type` → `kind` in v0.7.0; that's a wire-break. R10's job is purely additive: add `schema_version: int = 1` to `_Base` so every existing payload inherits. R3's job is the InboxMessage-level `message_kind` discriminator — distinct field, distinct concern. | Don't unify `type`/`kind` in this batch (out of scope per 09 §6 D12). |
| `lifecycle.py`, `runner.py` | **DO NOT LIFT.** Phase-2 territory. | Production loops own lifecycle; lifting now is a refactor we explicitly defer. |

**Net new files in /src/ from this batch:**
- `src/claude_anyteam/capabilities.py` (R11+R12+R13+R14 helpers + Pydantic models)
- `src/claude_anyteam/events.py` (R16 envelope models)
- (events sink helpers added inline to `protocol_io.py`)

No new on-disk channel beyond `events/<agent>.jsonl` (R16) and `manifests/<agent>.json` (R12) — both directly per 07 §5.5 and 09 R12 spec.

---

## 2. Ordering recommendation across the 8 work items

Hard dependency edges (per 09 §4 + 09 R-item prereqs):

```
R1  ─┐
R10 ─┼─► R2 ─► R11 ─┬─► R12 ─┬─► R13
W7  ─┘             │        ├─► R14
                   │        └─► R15 ◄─ R16 ◄─ R3
                   └─► W3 (R21)
                                R6 ◄─ R10
```

Soft edges: R3 → R16 (envelope events landing in inbox declare their `message_kind`); R4 → R16 (events/.lock timeout — bundle minimal R4 into R16 PR).

**Recommended four-wave parallel execution:**

| Wave | Items | Implementer | Blocking on |
|---|---|---|---|
| **1 (parallel, hour 0)** | R1, R10, W7-audit | foundation | none |
| **2 (parallel, hour ~2)** | R2, R3, R6, W3-design | foundation, shutdown, peer | wave 1 (R10 for R6) |
| **3 (parallel, hour ~4)** | R11, R16 (+ minimal R4) | cap, events | wave 2 (R2 for R11; R3 for R16) |
| **4 (parallel, hour ~8)** | R12, R15 | broadcast, peer | wave 3 (R11 for both; R16 for R15) |
| **5 (parallel, hour ~12)** | R13, R14, R21 (W3 matrix) | broadcast, peer | wave 4 (R12 for R13/R14) |

**Per-implementer assignments (matches the 6 codex-impl-* names team-lead briefed):**

- `codex-impl-foundation` → R1, R2, R3, R10. Tasks #12 + #13. Touches `messages.py`, `models.py` (substrate), `protocol_io.py`, `codex.py:47-49`, `pyproject.toml`. **Critical:** verify Pydantic round-trip for the new `message_kind` field on InboxMessage BEFORE merging R3 (per 09 R3 risk). If `mark_as_read` strips it, escalate to me — R3 promotes from S to M with a fall-back to ack-by-`messageId` (touches L12).
- `codex-impl-shutdown` → R6. Task #14. Touches `messages.py:58-69`, `protocol_io.py:185-198`. Keep legacy `shutdown_response` emit path with deprecation warn-log. Depends on R10 landing first (so the migration is versioned).
- `codex-impl-cap` → R11. Task #15. Touches `claude_teams/models.py` (substrate, additive), `registration.py:96-187` (write per-backend cap list), `codex.py`/`backends/gemini/loop.py`/`backends/kimi/loop.py` (declare flag set per CD-1..CD-6 in 09 R11 taxonomy), `team_cli.py:331-381` (`capabilities=` column). Depends on R2 (default-factory pattern protects the new field from one-bad-row).
- `codex-impl-events` → R16. Task #16. Touches new `src/claude_anyteam/events.py`, `protocol_io.py` (append_event + read_events), `_filelock.py` (minimal R4 — 30s timeout + staleness log). **Do NOT wire emitters in this batch** (R17/R18/R19 are out of scope); ship envelope shell + helpers + tests only. Depends on R3 (so envelope events that mirror to inbox declare `message_kind=visibility_event`).
- `codex-impl-broadcast` → R12 + R13. Task #17. Touches `registration.py` (write own manifest at register-time to `manifests/<agent>.json`), new `src/claude_anyteam/capabilities.py` cache module, `wrapper_server.py:54-68` (add `mcp_anyteam_capability_manifest` to EXPOSED_TOOLS). Use `capability_manifest_updated` envelope kind from R16 to invalidate peer caches. Depends on R11 + R16.
- `codex-impl-peer` → R15 + W3 (R21) + W7 (R22) + R14. Task #18 + Task #19. Touches `backends/{gemini,kimi}/loop.py:_handle_steer`, `codex.py:SteerQueue.push` (R15: capability-declared steer auth), `wrapper_server.py:178-243` (W3 audit), `backends/gemini/loop.py:289-291` (W7 verify), and `prompts.py` × 3 (R14 inject peer prompt fragments). Depends on R12 cache.

**Eight-hour parallelism:** wave 1 + wave 2 give us 7 of 8 items in flight by hour ~2; full critical-path completion when R12 → R13/R14 lands, ~12 hours of human-equivalent work. Codex executors at xhigh effort can compress this further; expect first PRs from foundation within hour 1.

---

## 3. Open design questions for you to weigh in on

**Q1 (must answer before R3 ships).** Pydantic-strip risk on the new `InboxMessage.message_kind` field. Per 09 R3 risk note: substrate's mark-as-read deserializes all messages, mutates `read`, and rewrites the array. With `model_config = {"populate_by_name": True}` only (no `extra="allow"`), pydantic strips unknown top-level fields on round-trip. **Decision:** do we (a) add `extra="allow"` to `InboxMessage` (substrate divergence, but the safe fix); (b) make `message_kind` an explicit declared field (preserves it but only because it's declared); (c) fall back to ack-by-`messageId` per L12 (R3 promotes S → M)? My recommendation: **(b) — declare `message_kind` explicitly with `default="peer_dm"`**, since R3's spec already requires the field. It preserves itself by construction. Confirm and I'll bake it into the codex-impl-foundation prompt.

**Q2 (R16 closed Literal scope).** Prototype's `EventKind` has 7 entries; 07 §7.2 spec lists 10 (adds `steer_ack`, `capability_changed`, `capability_manifest_updated`). Recommend shipping all 10 in v0.7.0 even though emitters for `steer_ack` (Codex `turn/steer` ack) and `capability_changed` (R12) won't all wire this batch — reserves the kind names so peers don't have to handle a v2 schema bump for them later. Acceptable?

**Q3 (R12 manifest storage location).** Prototype writes peerManifestCache inline in config.json under each member row. R12 spec writes per-teammate files at `manifests/<agent>.json`. Going with the spec (per-teammate files) — flagging because it's an obvious deviation from the prototype if anyone reads both. The spec choice avoids n×n config bloat and a write-amplification race against R5's shared config lock.

**Q4 (Codex SteerQueue peer-accept fix in R15).** Today Codex App Server's `SteerQueue.push` accepts any sender unintentionally (CD-6 footnote). R15 spec says align with the new capability-declared rule. Concretely: declare `accepts_peer_steer: false` for Codex by default and enforce at the wrapper. **This is a behavior change to a working path** — strictly speaking it tightens current permissive behavior. Per §1 this is a policy capability not a technical limitation, so should be declared not assumed. Going with default-false unless you say otherwise.

**Q5 (R6 host-binary acceptance).** R6 splits `shutdown_response` into `shutdown_approved`/`shutdown_rejected` keeping the legacy as a deprecated alias. 09 §3 R6 risk: codex-extract 03 catalog confirms host accepts the host-canonical names but doesn't confirm `shutdown_response` will be accepted forever. Does opus-rev-impl have a host-binary `isStructuredProtocolMessage` recognizer fixture to test acceptance? If not, ship the alias and audit in v0.7.1 — the deprecation window protects us. Flagging so we don't get blindsided.

**Q6 (out-of-scope items I'd pull in if you allow).** R4 (filelock 30s timeout) — already justified bundling minimal version into R16. R7 (`backend_exit_code` + `backend` field on TaskCompleteOut) — small, decouples Codex from Gemini/Kimi `task_complete` payloads and is a clean §1 win (each harness's identity visible). Both are S-sized. Permission to bundle into the current batch as opportunistic adds, or hold for the next wave?

---

## Cadence

Next checkpoint to team-lead: when codex-impl-foundation's first commit (R1 or R10) lands (expect ~hour 1–2). After that: per-merge one-liners + an 8-hour rollup checkpoint here as `daily-2-arch.md`.

---

## Q1–Q6 resolutions (received from team-lead, ~T+0:30)

- **Q1** → option (b): explicit `message_kind` field on InboxMessage with `default="peer_dm"`. Closed taxonomy at the type system level. Bake into codex-impl-foundation's R3 prompt.
- **Q2** → yes, ship all 10 kinds from 07 §7.2 in v0.7.0. Reserves names; emitters wire later.
- **Q3** → yes, per-teammate `manifests/<agent>.json`. Document deviation in code comment citing this thread.
- **Q4** → Per-backend initial `accepts_peer_steer` values:
  - Codex App Server: `false` (flip to true post-verification)
  - Codex exec: `false`
  - Gemini ACP: `true`
  - Gemini headless: `false`
  - Kimi headless: `false`
  - Subtle: protocol-side default = any_peer (carries declaration); Codex-side declaration = false (until verified). Both correct + consistent.
- **Q5** → no host-binary fixture; ship deprecated `shutdown_response` alias. Document risk in R6 commit; audit in v0.7.1 if rejection signals.
- **Q6** → bundle both. R4 (30s filelock timeout) goes into codex-impl-events alongside R16. R7 (`backend` + `backend_exit_code` on TaskCompleteOut) goes into codex-impl-foundation alongside R10's schema_version sweep.

## PE-1 in effect during this batch

Implementers can't peer-DM yet (R21/W3 fix is in this batch). Team-lead acts as pivot for cross-implementer coordination; I send implementer briefs to team-lead as message payloads for relay.
