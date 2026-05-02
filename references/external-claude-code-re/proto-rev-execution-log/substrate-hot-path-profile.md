# Substrate hot-path profile (#60)

Date: 2026-04-28  
Integration anchor: `144b4f6` (`144b4f60d828` in benchmark worktree)  
Command: `python tools/benchmark/substrate_hot_path_profile.py --json`

## Scope

This is a synthetic micro-benchmark for the Python substrate paths suspected in the M11a p50 regression (178s full-stack vs 69s 3-knob ablation in S8+W7 #49 vs #52). It isolates CPU-side hot paths only; it does **not** include teammate process startup, backend LLM latency, config-file reads, mailbox writes, or event-log fanout.

Fixture: S8-like in-memory roster with one requester plus seven peers using mixed Codex App Server, Gemini ACP, and Kimi headless Agent Cards.

Environment:

- Python: `3.12.3`
- Platform: `linux`
- Samples: `401`
- Roster size: `8` total / `7` peers
- R14 fragment size: `16858` bytes
- Task prompt size without R14: `2486` bytes
- Task prompt size with R14: `19346` bytes
- `CLAUDE_ANYTEAM_DISABLE_MANIFEST_CACHE`: `None`
- `CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS`: `None`
- Peer-steer manifest check enforced: `True`

## Results

| Component | Iterations/sample | Median μs/call | p95 μs/call | Notes |
| --- | ---: | ---: | ---: | --- |
| `cache_get_warm` | 1000 | 0.099 | 0.164 | CapabilityManifestCache.get(team, agent) equivalent on a warmed in-memory cache. |
| `steer_gate_single_authorized` | 1000 | 3.538 | 4.419 | Manifest-gated send_message(kind='steer') authorization for one peer that accepts peer steer. |
| `steer_gate_broadcast_mixed_7_peers` | 1000 | 9.850 | 12.379 | Manifest-gated steer authorization for '*' fanout over seven peers, including denying peers. |
| `r14_fragment_composition_s8` | 100 | 81.101 | 97.162 | R14 peer-prompt-fragment aggregation over an S8-like manifest cache. |
| `prompt_construct_without_r14` | 1000 | 0.778 | 1.066 | Codex v7 task prompt construction with no peer fragments. |
| `prompt_construct_with_precomputed_r14` | 1000 | 4.682 | 5.259 | Codex v7 task prompt construction with a precomputed R14 fragment string. |
| `prompt_construct_with_fresh_r14` | 100 | 87.769 | 100.082 | End-to-end prompt construction when each call recomposes R14 fragments. |

## Interpretation

- Warm `CapabilityManifestCache.get()` is effectively a dict lookup: median `0.099` μs/call, p95 `0.164` μs/call. This does **not** explain a seconds-scale p50 regression.
- The manifest-gated `send_message(kind='steer')` authorization branch is also tiny: median `3.538` μs/call for one accepting peer, and median `9.850` μs/call for `*` fanout over seven peers. Even thousands of checks would remain sub-100ms CPU time.
- R14 fragment composition is the heaviest isolated Python path measured, but still sub-millisecond: median `81.101` μs/call, p95 `97.162` μs/call for an S8-like cache.
- Prompt construction itself is cheap: median `0.778` μs/call without R14 and `4.682` μs/call with a precomputed R14 fragment. Recomputing R14 on every prompt gives median `87.769` μs/call.
- The meaningful R14 signal is not Python CPU; it is prompt growth. The fixture adds `16858` bytes of peer-capability text, growing the task prompt from `2486` bytes to `19346` bytes. If stress-run ablations implicate R14, the likely mechanism is backend/model prompt ingestion and generation behavior, not local fragment assembly.

## Verdict

This profile refutes `CapabilityManifestCache.get()`, sender-side manifest steer gating, and local prompt/string composition as direct CPU contributors to the 109s p50 delta. It remains consistent with a token/context-size hypothesis for R14: the local code spends ~0.1ms composing fragments, but injects ~16.9KB of additional instructions into each relevant backend turn.
