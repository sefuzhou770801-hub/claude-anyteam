# M13 collision investigation — S6-W7-20260428T1326Z-postall-3600s

Task: #50 (`codex-collisions`)  
Integration HEAD: `2900940`

## What M13 currently measures

`tools/stress/score_collab.py:is_collision()` increments the terminal-side M13 candidate when all of these are true:

1. event kind is `turn_completed` or `turn_failed`;
2. `payload.structured is not True`;
3. `int(payload.tool_call_events) > 0`;
4. `len(payload.last_message_preview.strip()) > 32`.

The aggregate currently sums those terminal candidates, then `m13_attribution_records()` tries to attribute each candidate to the first same-agent terminal after a wrapper `send_message` event. If no wrapper `send_message` is in that window, the scorer emits a `recipient: "N/A"` collision record.

## The five records in the scored output

`collab/scenario.json` reports `M13_total_collisions = 5`; every attribution is `codex-app-server -> N/A`, i.e. the scorer did **not** find a wrapper `send_message` reply in the turn window.

| # | terminal event | agent | terminal time | final preview | tool activity in that prose turn |
|---|---|---|---|---|---|
| 1 | `codex-pair-a:019dd44f-7677-7a23-ae79-eebfacff293b:000006` | `codex-pair-a` | 2026-04-28T13:38:18.431Z | `I don’t have a visible \`send_message\` MCP tool in this session.` | `list_mcp_resources` only |
| 2 | `codex-pair-b:019dd450-ef51-7cc2-ae78-19df328bc1e8:000006` | `codex-pair-b` | 2026-04-28T13:39:56.793Z | `Thanks—great work. Clean branch with passing tests noted; this looks ready for review/next steps.` | `list_mcp_resources` only |
| 3 | `codex-pair-b:019dd473-ae91-75b0-91c2-94860694c470:000020` | `codex-pair-b` | 2026-04-28T14:19:10.595Z | `No \`send_message\` MCP tool is available in this session’s toolset.` | `list_mcp_resources`, `true`, repeated Canva no-op searches |
| 4 | `codex-pair-a:019dd486-0e00-7791-9bad-b923c32f0f2a:000008` | `codex-pair-a` | 2026-04-28T14:38:07.286Z | `Acknowledged; I’ll handle the \`relative_path\` addition as requested and DM codex-pair-b when it’s ready.` | `list_mcp_resources`, `list_mcp_resource_templates` |
| 5 | `codex-pair-a:019dd486-a74f-74d2-a9bf-ba3918929274:000026` | `codex-pair-a` | 2026-04-28T14:39:25.948Z | `Message delivered to codex-pair-b.` | shell discovery, then direct `claude_teams.messaging.send_plain_message(...)` |

All five `turn_started` rows have `payload.mode = "prose"` and `payload.prompt_kind = "prose_reply"`. They are idle prose-reply turns, not task turns.

## Cross-check against steer/L4/manifest-gate

The run does **not** show peer steers slipping through:

- `M9_delivery_breakdown` is zero for both agents.
- `peer_steer_rejected_count` is zero.
- Searching `codex-pair-{a,b}.jsonl` for `peer_steer`, `turn/steer`, and `steer` finds only the task watchdog's lead-like checkpoint steer plus source-code inspections of steer enforcement.
- The collision turns have `prompt_kind="prose_reply"`; they were not produced by `_execute_task_app_server()`'s mid-turn inbox hook.

For the peer messages that preceded these prose turns, the observed/inferrable discriminator values are ordinary peer DM values:

| Collision turn | Likely triggering peer DM(s) | sender -> recipient | messageKind on send path | L4 `_mid_turn_prose_should_be_steer` result | manifest-gated peer-steer result |
|---|---|---|---|---|---|
| `019dd44f-7677-7a23-ae79-eebfacff293b` | `grep API surface approved` | `codex-pair-b -> codex-pair-a` | `informational` | `False` (peer + not `steer`) | not invoked; Codex App Server manifest is lead-only / no `accepts_peer_steer` |
| `019dd450-ef51-7cc2-ae78-19df328bc1e8` | `API/test handoff ack` | `codex-pair-a -> codex-pair-b` | legacy/omitted (`peer_dm` default on model) | `False` (peer + not `steer`) | not invoked; recipient manifest denies peer steer |
| `019dd473-ae91-75b0-91c2-94860694c470` | ordinary task #3/#4 coordination DMs | `codex-pair-a -> codex-pair-b` | `informational` | `False` (peer + not `steer`) | not invoked; recipient manifest denies peer steer |
| `019dd486-0e00-7791-9bad-b923c32f0f2a` | task #5 implementation/test handoff DMs | `codex-pair-b -> codex-pair-a` | `informational` | `False` (peer + not `steer`) | not invoked; recipient manifest denies peer steer |
| `019dd486-a74f-74d2-a9bf-ba3918929274` | `task 5 implementation complete` arriving during the previous prose reply | `codex-pair-b -> codex-pair-a` | `informational` | not applicable to prose-reply turn; would be `False` | not invoked; recipient manifest denies peer steer |

## Root cause

There are two layered causes, neither of which is a failed L4/manifest peer-steer gate:

1. **M13 scorer over-counts prose-reply turns that merely used any tool.** Four of the five records have no wrapper `send_message` reply in the turn; the model only tried discovery/no-op tools and then emitted final prose. The existing `is_collision()` predicate treats that as a collision because it keys on `tool_call_events > 0`, even though no structured peer reply was delivered.

2. **One real duplicate-risk path bypassed the wrapper tool.** In turn `019dd486-a74f-74d2-a9bf-ba3918929274`, Codex could not find a callable `send_message` tool, used shell/Python to call `claude_teams.messaging.send_plain_message(...)`, and then emitted final prose (`Message delivered to codex-pair-b.`). Runtime `should_skip_prose_fallback()` only recognizes wrapper `send_message` tool calls, so it would not suppress the adapter's prose fallback after this direct delivery.

Why the models bypassed the wrapper is visible in several turns: they ran `list_mcp_resources`, `command -v send_message`, and source greps, concluded no visible `send_message` MCP tool was available, then sometimes constructed the wrapper manually through shell. That is a tool-discovery/usability problem, but it is separate from the peer-steer discriminator.

## Fix direction

- Tighten M13 scoring so a terminal prose fallback only counts as a collision when the same same-agent turn window includes an actual wrapper `send_message` peer delivery. Do not emit `recipient=N/A` collision records for generic tool-use prose turns.
- Extend the runtime delivered-via-tool guard to recognize successful host-command direct deliveries via `claude_teams.messaging.send_plain_message(...)` / `append_message(...)` (and shell-built `call_tool('send_message', ...)`) so the adapter suppresses fallback prose if Codex uses that workaround.
- Add regression tests for both cases: generic tool discovery with prose is not M13, and direct peer delivery suppresses prose fallback detection.
