# Kimi runtime research probes

These transcripts are **not** parser-contract fixtures (those live one directory
up under `tests/fixtures/kimi/*.jsonl` per the contract in
`tests/fixtures/kimi/README.md`). They are empirical evidence captured by
kimi-architect during Task #1 to back specific claims in
`docs/internal/kimi-integration/kimi-runtime.md`. Tests **may** consume them as
secondary evidence, but the canonical fixtures for assertion are the
contract-named ones.

Capture environment: `kimi 1.39.0` (`agent spec versions: 1`, `wire protocol
1.9`), `KIMI_CLI_NO_AUTO_UPDATE=1`. Absolute paths redacted to `/REDACTED/`
and the local username to `REDACTED`. Session IDs are not in stdout so no
redaction is needed inside the JSONL.

## Inventory

### `final_message_only_no_tool.jsonl`

Backs `kimi-runtime.md §Wire-format notes › Final-message-only shape`.

Invocation:
```bash
KIMI_CLI_NO_AUTO_UPDATE=1 kimi --print --output-format=stream-json \
  --work-dir tests/fixtures/kimi/workspaces/empty --no-thinking \
  --max-steps-per-turn 1 --final-message-only \
  -p 'Reply exactly: FINAL_ONLY_NO_TOOL_OK'
```

What it proves: when `--final-message-only` is passed alongside
`--output-format=stream-json`, the per-message NDJSON collapses to a **single
line** whose `content` is a **plain string**, not an array of `think`/`text`
parts. No `tool_calls`, no intermediate `assistant`/`tool` messages. The
parser must branch on `isinstance(content, str)` vs `list` to extract
`last_message`.

### `final_message_only_with_tool.jsonl`

Same backing claim. Demonstrates that even when the model genuinely calls a
tool (`Shell cat sentinel.txt`) inside the turn, `--final-message-only` strips
the assistant `tool_calls` lines and the `role:tool` lines from stdout. The
adapter therefore **cannot count `tool_call_events` from
`--final-message-only` runs** — it must omit the flag for any invocation that
needs the tool-call telemetry.

### `max_steps_overflow.jsonl`

Backs `kimi-runtime.md §Capability matrix` (Plan-mode auto-exits / non-JSON
stdout terminator) and §Stream-json wire-format notes.

Invocation:
```bash
KIMI_CLI_NO_AUTO_UPDATE=1 kimi --print --output-format=stream-json \
  --work-dir tests/fixtures/kimi/workspaces/empty --no-thinking \
  --max-steps-per-turn 1 \
  -p 'Use the Shell tool to run pwd, then use Shell again to run whoami, then reply exactly: MAX_STEPS_DONE'
```

Exit code: **1**. What it proves:

1. When the step limit is reached, kimi prints `Max number of steps reached: 1`
   as a **plain stdout line** (not JSON, not stderr) **after** the last valid
   NDJSON record. The parser must tolerate a trailing non-JSON line on stdout,
   not just non-JSON preamble lines (Gemini-style banner pollution).
2. The truncated tool-call landmine: when the limit fires mid-emission, the
   second `tool_calls[*].function.arguments` field can contain **invalid
   JSON** (here: `"{\"command"`). Adapter code that re-parses
   `arguments` (e.g. for sandboxing or argument inspection) must handle
   malformed JSON without crashing the turn.
3. `tool_call_events` would still count both `tool_calls` entries because
   they appear in the assistant line. Test code asserting tool-call counts
   should accept overflow-truncated runs as legitimate `>= 2` rather than
   demand parse-clean tool-call payloads.
